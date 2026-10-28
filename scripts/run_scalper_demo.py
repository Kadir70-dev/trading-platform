#!/usr/bin/env python3
"""Live XAUUSD scalper runner — Exness DEMO account, REAL MT5 backend only.

Discretionary let-winners-run / cut-losers-fast-and-flip behavior (see
scalper/state_machine.py, scalper/exit.py). No hedging — disabled until the
base strategy is profitable on its own.

Must run under the Wine Windows Python (the MetaTrader5 package only works
there); see docs/MT5_LINUX_WINE.md for the invocation. Never uses SimBroker:
it instantiates MT5Broker directly, hard-aborts before ever trading if the
account isn't DEMO, and stops immediately if the terminal disconnects.

On every fill/close it prints: timestamp, symbol, side, lot, entry, SL, TP,
ticket, deal ID, broker retcode, and current balance/equity. Every flip also
logs old direction, old entry, close price, realized P&L, the reversal signal
reason, new direction, and new entry. A STOP file in the run directory
triggers a graceful shutdown (mirrors scripts/run_pepperstone_demo.py).
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from config import get_settings  # noqa: E402
from mt5.client import MT5Broker  # noqa: E402
from mt5.connection import MT5Connection  # noqa: E402
from scalper.broker_adapter import BrokerAdapter  # noqa: E402
from scalper.config import ScalperParams  # noqa: E402
from scalper.state_machine import ScalperStateMachine  # noqa: E402
from scalper.types import Position, PositionState  # noqa: E402
from scalper import reconciliation as rec  # noqa: E402
from scalper import execution_safety as es  # noqa: E402
from scalper import sessions as sess  # noqa: E402
from scalper.notify import TelegramNotifier  # noqa: E402
from scalper.process_guard import SingleInstanceLock  # noqa: E402

POLL_SEC = 5  # how often to check for a newly closed bar
# Step 3/4 account/runtime config (env-overridable; NOT strategy parameters).
RISK_PCT = float(os.getenv("SCALPER_RISK_PER_TRADE_PCT", "0.005"))   # 0.5% default
RECOVERY_POLICY = os.getenv("SCALPER_RECOVERY_POLICY", "manual")      # manual | adopt
LOCK_PATH = os.getenv("SCALPER_LOCK_PATH", str(ROOT / "data" / "scalper-demo" / "scalper.lock"))
SESSION_GATE = os.getenv("SCALPER_SESSION_GATE", "1") != "0"         # block entries off-session


def log_line(**fields) -> None:
    fields.setdefault("ts", datetime.now(timezone.utc).isoformat())
    print(json.dumps(fields, default=str), flush=True)


def connect_real_mt5():
    s = get_settings()
    if s.account_mode.upper() != "DEMO":
        log_line(event="ABORT", reason=f"ACCOUNT_MODE={s.account_mode}, demo runner refuses non-DEMO")
        sys.exit(1)
    conn = MT5Connection(s.mt5_login, s.mt5_password, s.mt5_server, s.mt5_path)
    if not conn.connect():
        log_line(event="ABORT", reason="mt5 connect failed")
        sys.exit(1)
    ident = conn.identity()
    if ident is None or ident.mode != "DEMO":
        log_line(event="ABORT", reason=f"broker identity mode={ident.mode if ident else None} != DEMO — "
                                        "refusing to trade a non-DEMO account")
        conn.shutdown()
        sys.exit(1)
    ok, problems = conn.verify(s.leverage, s.account_mode)
    if not ok:
        log_line(event="ABORT", reason=f"verification failed: {problems}")
        conn.shutdown()
        sys.exit(1)
    return conn, MT5Broker(conn)


def still_connected_and_demo(conn: MT5Connection) -> bool:
    ident = conn.identity()
    return ident is not None and ident.mode == "DEMO"


STALE_BAR_TIMEOUT_SEC = 90  # observed: the Wine MT5 terminal's own bar-builder can
                            # silently freeze (ticks keep flowing, M1 history stops
                            # advancing) — a Python-side IPC reconnect does NOT fix
                            # this, since a fresh mt5.initialize() sees the same
                            # frozen bar array. Only a full terminal restart does.
                            # Exit distinctly so the shell supervisor restarts the
                            # whole Wine/terminal stack, not just this process.
STALE_BAR_EXIT_CODE = 42


def wait_for_next_closed_bar(broker: MT5Broker, conn: MT5Connection, symbol: str,
                              timeframe: str, last_bar_time):
    started = time.monotonic()
    while True:
        raw = broker.ohlc(symbol, timeframe, 300)  # includes the currently-forming bar at the end
        closed = raw.iloc[:-1]
        if len(closed) and (last_bar_time is None or closed["time"].iloc[-1] > last_bar_time):
            return closed
        if time.monotonic() - started > STALE_BAR_TIMEOUT_SEC:
            log_line(event="WARN_STALE_BAR_TERMINAL_FROZEN",
                      reason=f"no new {timeframe} bar in {STALE_BAR_TIMEOUT_SEC}s — MT5 terminal's bar "
                              "builder appears frozen; exiting for a full restart")
            conn.shutdown()
            raise SystemExit(STALE_BAR_EXIT_CODE)
        time.sleep(POLL_SEC)


def main() -> int:
    params = ScalperParams()  # reads SCALPER_* from .env (symbol=XAUUSDm, lot=0.27, hedge disabled)
    if params.hedge_enabled:
        log_line(event="ABORT", reason="hedging is disabled for this runner until the base "
                                        "strategy is profitable — set SCALPER_HEDGE_ENABLED=false")
        sys.exit(1)

    # Single-instance lock: a VPS reboot / Task Scheduler double-fire must never
    # start a second bot against the same account (duplicate-entry prevention).
    lock = SingleInstanceLock(LOCK_PATH)
    lk = lock.acquire()
    if not lk.acquired:
        log_line(event="ABORT", reason=f"another scalper instance is running (owner pid={lk.owner_pid})")
        sys.exit(1)

    notifier = TelegramNotifier.from_env()   # disabled unless token+chat in env
    conn, broker = connect_real_mt5()
    adapter = BrokerAdapter(broker, params)
    notifier.broker_connection(True)
    # pnl_mode="broker": the strategy never books its own leg_pnl into the risk
    # guard. We reconcile the real MT5 deals and feed the authoritative realized
    # figure in, so a simulated number can never drive live risk controls.
    sm = ScalperStateMachine(params, symbol=params.symbol, pnl_mode="broker")
    ledger = rec.ReconcileLedger()

    run_dir = ROOT / "data" / "scalper-demo" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir.mkdir(parents=True, exist_ok=True)
    stop_file = run_dir / "STOP"

    primary_ticket: int | None = None
    acct = broker.account_info()
    ident = conn.identity()
    info = broker.symbol_info(params.symbol)
    log_line(event="PRE_FLIGHT_CHECK",
              login=acct["login"], server=acct["server"], mode=ident.mode,
              symbol=params.symbol, bid=info["bid"], ask=info["ask"],
              spread_points=info["spread_points"], trade_allowed=info["trade_allowed"],
              lot_size=params.lot_size, balance=acct["balance"], equity=acct["equity"])
    if ident.mode != "DEMO" or not info["trade_allowed"]:
        log_line(event="ABORT", reason=f"pre-flight failed: mode={ident.mode} trade_allowed={info['trade_allowed']}")
        conn.shutdown()
        return 1

    # Phase A — verify broker symbol spec; without it we cannot size safely.
    pf = es.preflight(broker, params.symbol, expect_mode="DEMO",
                      ident_mode=ident.mode, trade_allowed=info["trade_allowed"])
    if not pf.ok:
        log_line(event="ABORT", reason=f"preflight blocked: {pf.reason} (cannot verify symbol spec)")
        conn.shutdown()
        return 1
    spec = pf.spec
    log_line(event="SYMBOL_SPEC", symbol=params.symbol, point=spec.point, tick_size=spec.tick_size,
              tick_value=spec.tick_value, contract_size=spec.contract_size,
              volume_min=spec.volume_min, volume_max=spec.volume_max, volume_step=spec.volume_step)

    # Phase C — restart recovery (read-only; never closes/modifies a position).
    plan = es.startup_recovery(broker, params.magic_number, params.symbol, policy=RECOVERY_POLICY)
    log_line(event="RECOVERY", action=plan.action, reason=plan.reason,
              ours=len(plan.positions), orphans_no_sl=len(plan.orphans_no_sl),
              foreign=len(plan.foreign_positions), state_known=plan.state_known)
    # Only protected, unambiguous single-position recovery may be adopted.
    # Hold-age reconstruction waits for actual broker M1 candle timestamps.
    pending_adopt = (plan.positions[0]
                     if plan.action == es.RECOVER_ADOPT and plan.state_known
                     and len(plan.positions) == 1 else None)
    if pending_adopt:
        primary_ticket = pending_adopt["ticket"]
    if not plan.state_known:
        # MANUAL / BLOCKED / orphan-without-SL => fail closed: monitor only, no new entries.
        sm.guard.set_broker_state_known(False, reason=plan.reason)
        notifier.manual_intervention(plan.reason)

    log_line(event="STARTED", login=acct["login"], server=acct["server"], symbol=params.symbol,
              risk_pct=RISK_PCT, recovery=plan.action, magic=params.magic_number, run_dir=str(run_dir))
    notifier.startup(params.symbol, ident.mode, acct["balance"])

    last_bar_time = None
    try:
        while True:
            if stop_file.exists():
                log_line(event="STOP_FILE_DETECTED")
                break
            if not still_connected_and_demo(conn):
                log_line(event="EMERGENCY_STOP", reason="MT5 disconnected or account mode changed away from DEMO")
                break

            info = broker.symbol_info(params.symbol)
            if not info["trade_allowed"]:
                log_line(event="HOLD", reason="symbol not tradeable right now")
                time.sleep(POLL_SEC)
                continue

            previous_closed_bar = last_bar_time
            bars = wait_for_next_closed_bar(broker, conn, params.symbol, "M1", previous_closed_bar)
            last_bar_time = bars["time"].iloc[-1]
            now = datetime.now(timezone.utc)
            spread_points = info["spread_points"]

            # A rolling 300-bar DataFrame has a nearly constant len(df)-1.
            # Advance the existing position's age using actual newly closed
            # broker bars, never wall-clock time or a fabricated 1-bar tick.
            if sm.position is not None and previous_closed_bar is not None:
                try:
                    age_delta = es.advance_position_clock(
                        sm.position, previous_closed_bar, bars["time"])
                    log_line(event="HOLD_CLOCK_ADVANCED", new_closed_bars=age_delta)
                except ValueError as exc:
                    log_line(event="RECOVERY_BLOCKED", reason=str(exc))
                    notifier.manual_intervention("M1 holding clock became unverifiable")
                    break

            if pending_adopt is not None:
                try:
                    bar_index = len(bars) - 1
                    bar_dt = last_bar_time.to_pydatetime()
                    if bar_dt.tzinfo is None:
                        bar_dt = bar_dt.replace(tzinfo=timezone.utc)
                    bar_open_sec = int(bar_dt.timestamp())
                    opened_ms = pending_adopt.get("time_msc")
                    if not opened_ms and pending_adopt.get("time"):
                        opened_ms = int(pending_adopt["time"]) * 1000
                    if not opened_ms or opened_ms <= 0:
                        raise ValueError("missing broker opening timestamp")
                    deals = broker.history(days=30)
                    m1_opens = broker.m1_bar_opens(
                        params.symbol, (int(opened_ms) // 60000) * 60, bar_open_sec)
                    adopted = es.adopt_position(
                        params, pending_adopt, bar_index,
                        history_deals=deals, m1_bar_opens_sec=m1_opens,
                        current_bar_open_sec=bar_open_sec)
                except Exception as exc:
                    # NEVER adopt with an invented age; no automatic close or
                    # protective order modifications on ambiguous recovery.
                    log_line(event="RECOVERY_BLOCKED", reason=str(exc))
                    sm.guard.set_broker_state_known(False, reason="adoption metadata unverified")
                    notifier.manual_intervention("adoption requires manual review")
                    break
                sm.position = adopted
                sm.state = adopted.direction
                log_line(event="ADOPTED", ticket=pending_adopt["ticket"],
                         bar_index=bar_index,
                         bars_held=bar_index - adopted.entry_bar_index,
                         entry_time=adopted.entry_time.isoformat(),
                         side=adopted.direction.value)
                pending_adopt = None

            # Session gate: entries only during valid Gold sessions (monitoring
            # and exits always run). Broker trade_allowed above is the hard gate;
            # this blocks weekends/maintenance so no entry is attempted at rollover.
            in_session = (not SESSION_GATE) or sess.entries_allowed(now)

            decisions = sm.on_bar(bars, spread_points, now)

            for d in decisions:
                if not still_connected_and_demo(conn):
                    log_line(event="EMERGENCY_STOP", reason="MT5 disconnected mid-decision")
                    notifier.emergency_stop("MT5 disconnected mid-decision")
                    return 1

                if d.event.startswith("ENTER_"):
                    if not in_session:
                        log_line(event="ENTRY_SKIPPED", reason="SESSION_CLOSED")
                        sm.state = PositionState.FLAT   # roll back un-executed entry
                        sm.position = None
                        primary_ticket = None
                    else:
                        primary_ticket = _sized_open(broker, adapter, sm, params, spec, d, now, last_bar_time, None, notifier)
                elif d.event.startswith("EXIT_"):
                    _execute_close(broker, sm, ledger, primary_ticket, d.reason_code, d.detail, now, notifier)
                    primary_ticket = None
                elif d.event.startswith("FLIP_"):
                    if not in_session:
                        log_line(event="ENTRY_SKIPPED", reason="SESSION_CLOSED_FLIP")
                        sm.state = PositionState.FLAT   # old leg already closed by EXIT handler
                        sm.position = None
                        primary_ticket = None
                    else:
                        primary_ticket = _sized_open(broker, adapter, sm, params, spec, d, now, last_bar_time, d.detail, notifier)

                log_line(event=d.event, reason_code=d.reason_code, state=d.state, detail=d.detail)
    finally:
        conn.shutdown()
        notifier.broker_connection(False)
        notifier.shutdown("runner exit")
        lock.release()
        log_line(event="STOPPED")
    return 0


def _sized_open(broker, adapter: BrokerAdapter, sm: ScalperStateMachine, params: ScalperParams,
                spec, decision, now, bar_time, flip_detail, notifier=None) -> int | None:
    """Risk-size and fail-closed-gate an entry the strategy has already decided.

    The strategy DECISION is untouched; if risk sizing / duplicate / state
    checks block it, the trade is SKIPPED and the state machine is rolled back
    to FLAT so internal state stays consistent with the broker (no phantom
    position). Never changes thresholds or signals."""
    position = sm.position
    acct = _safe(broker.account_info) or {}
    equity = acct.get("equity") or acct.get("balance") or 0.0

    sizing = es.plan_entry_lot(equity, RISK_PCT, spec, params,
                               price=position.entry_price, free_margin=acct.get("free_margin"),
                               leverage=params.leverage)
    positions = _safe(broker.positions)
    tag = es.bar_tag(params.symbol, bar_time)
    duplicate = positions is not None and rec.would_duplicate(positions, params.magic_number, params.symbol, tag)
    state_known = sm.guard.broker_state_known and positions is not None

    gate = es.entry_gate(preflight_ok=True, broker_state_known=state_known,
                         guard_ok=True, sizing=sizing, duplicate=duplicate)
    if not gate.allowed:
        log_line(event="ENTRY_SKIPPED", reason=gate.reason, sizing_reason=sizing.reason,
                  risk_budget=sizing.risk_budget_usd, computed_lot=sizing.lot, equity=equity)
        sm.state = PositionState.FLAT    # roll back the un-executed entry
        sm.position = None
        return None

    comment = f"scalper|{tag}"
    side = "BUY" if position.direction == PositionState.LONG else "SELL"
    res = _execute_open(broker, adapter, params, position, flip_detail, gate.volume, comment, sizing)
    if res is None:
        sm.state = PositionState.FLAT
        sm.position = None
        if notifier is not None:
            notifier.order_rejected(f"{side} open failed")
    elif notifier is not None:
        notifier.order_executed(side, gate.volume, position.entry_price, res)
    return res


def _execute_open(broker, adapter: BrokerAdapter, params: ScalperParams,
                  position, flip_detail, volume: float, comment: str, sizing) -> int | None:
    res = adapter.open(position.direction, position.entry_price, position.risk_unit,
                       comment=comment, volume=volume)
    acct = _safe(broker.account_info) or {}
    sl = (position.entry_price - position.risk_unit * params.emergency_stop_r_mult
          if position.direction == PositionState.LONG
          else position.entry_price + position.risk_unit * params.emergency_stop_r_mult)
    fields = dict(event="ORDER_OPEN", symbol=params.symbol,
                  side="BUY" if position.direction == PositionState.LONG else "SELL",
                  lot=volume, risk_usd=sizing.risk_usd, risk_budget=sizing.risk_budget_usd,
                  entry=res.price, sl=sl, tp=None,
                  ticket=res.ticket, retcode=res.retcode, retcode_desc=res.retcode_desc,
                  balance=acct.get("balance"), equity=acct.get("equity"))
    if flip_detail is not None:
        fields["flip"] = flip_detail
    log_line(**fields)
    if res.ok and res.ticket:
        position.ticket = res.ticket
        return res.ticket
    log_line(event="ORDER_REJECTED", retcode=res.retcode, retcode_desc=res.retcode_desc)
    return None


def _safe(fn):
    """Run a broker query, returning None if it raises — so a transient failure
    is surfaced as 'unknown' (TRANSIENT) rather than a fabricated empty result."""
    try:
        return fn()
    except Exception as exc:  # pragma: no cover - exercised via unit tests with a stub
        log_line(event="BROKER_QUERY_FAILED", error=str(exc))
        return None


def _execute_close(broker: MT5Broker, sm: ScalperStateMachine, ledger: "rec.ReconcileLedger",
                   ticket: int | None, reason_code: str, detail, now, notifier=None) -> None:
    """Close a position and reconcile the AUTHORITATIVE realized P&L from broker
    deals. Never books strategy-computed P&L; never treats an unknown outcome as
    zero; fails closed (blocks new entries) on a transient/absent outcome."""
    if ticket is None:
        return
    res = broker.close_position(ticket)

    reconciliation = rec.reconcile_with_retry(
        ticket,
        fetch_positions=lambda: _safe(broker.positions),
        fetch_deals=lambda: _safe(lambda: broker.history(days=1)),
        ledger=ledger,
        client_close_ok=res.ok,
    )

    acct = _safe(broker.account_info) or {}
    if reconciliation.status == rec.STATUS_CONFIRMED:
        ledger.book_tickets(reconciliation.booked_deal_tickets)
        sm.record_broker_realized(reconciliation.realized, now)   # broker truth -> risk guard
        sm.guard.set_broker_state_known(True)
        if notifier is not None:
            notifier.position_closed(ticket, reconciliation.realized, confirmed=True)
            if sm.guard.kill_switch:
                notifier.daily_loss_limit(sm.guard.daily_pnl)
    elif reconciliation.status == rec.STATUS_STILL_OPEN:
        # The close did not take; do NOT rebook or resend blindly. Leave the
        # position to the next bar's logic; state is still known.
        pass
    else:  # TRULY_ABSENT or TRANSIENT -> outcome unknown, fail closed
        sm.guard.set_broker_state_known(False, reason=reconciliation.detail)
        if notifier is not None:
            notifier.position_closed(ticket, None, confirmed=False)   # unknown — never a fake 0
            notifier.manual_intervention(reconciliation.detail)

    log_line(event="ORDER_CLOSE", ticket=ticket, close_price=res.price,
              close_reason=reason_code, close_detail=detail,
              retcode=res.retcode, retcode_desc=res.retcode_desc,
              reconcile_status=reconciliation.status,
              realized=reconciliation.realized,            # None when unknown — never a fake 0
              closed_by_broker=reconciliation.closed_by_broker,
              broker_state_known=sm.guard.broker_state_known,
              balance=acct.get("balance"), equity=acct.get("equity"))


if __name__ == "__main__":
    raise SystemExit(main())


if __name__ == "__main__":
    raise SystemExit(main())
