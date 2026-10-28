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
from scalper.types import PositionState  # noqa: E402
from scalper import reconciliation as rec  # noqa: E402

POLL_SEC = 5  # how often to check for a newly closed bar


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

    conn, broker = connect_real_mt5()
    adapter = BrokerAdapter(broker, params)
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
    log_line(event="STARTED", login=acct["login"], server=acct["server"], symbol=params.symbol,
              lot_size=params.lot_size, magic=params.magic_number, run_dir=str(run_dir))

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

            bars = wait_for_next_closed_bar(broker, conn, params.symbol, "M1", last_bar_time)
            last_bar_time = bars["time"].iloc[-1]
            now = datetime.now(timezone.utc)
            spread_points = info["spread_points"]

            decisions = sm.on_bar(bars, spread_points, now)

            for d in decisions:
                if not still_connected_and_demo(conn):
                    log_line(event="EMERGENCY_STOP", reason="MT5 disconnected mid-decision")
                    return 1

                if d.event.startswith("ENTER_"):
                    primary_ticket = _execute_open(broker, adapter, params, sm.position, None)
                elif d.event.startswith("EXIT_"):
                    _execute_close(broker, sm, ledger, primary_ticket, d.reason_code, d.detail, now)
                    primary_ticket = None
                elif d.event.startswith("FLIP_"):
                    primary_ticket = _execute_open(broker, adapter, params, sm.position, d.detail)

                log_line(event=d.event, reason_code=d.reason_code, state=d.state, detail=d.detail)
    finally:
        conn.shutdown()
        log_line(event="STOPPED")
    return 0


def _execute_open(broker: MT5Broker, adapter: BrokerAdapter, params: ScalperParams,
                  position, flip_detail: dict | None) -> int | None:
    res = adapter.open(position.direction, position.entry_price, position.risk_unit, comment="scalper")
    acct = broker.account_info()
    sl = (position.entry_price - position.risk_unit * params.emergency_stop_r_mult
          if position.direction == PositionState.LONG
          else position.entry_price + position.risk_unit * params.emergency_stop_r_mult)
    fields = dict(event="ORDER_OPEN", symbol=params.symbol,
                  side="BUY" if position.direction == PositionState.LONG else "SELL",
                  lot=params.lot_size, entry=res.price, sl=sl, tp=None,
                  ticket=res.ticket, retcode=res.retcode, retcode_desc=res.retcode_desc,
                  balance=acct["balance"], equity=acct["equity"])
    if flip_detail is not None:
        fields["flip"] = flip_detail  # old_direction/old_entry/close_price/realized_pnl/reversal_reason/...
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
                   ticket: int | None, reason_code: str, detail, now) -> None:
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
    elif reconciliation.status == rec.STATUS_STILL_OPEN:
        # The close did not take; do NOT rebook or resend blindly. Leave the
        # position to the next bar's logic; state is still known.
        pass
    else:  # TRULY_ABSENT or TRANSIENT -> outcome unknown, fail closed
        sm.guard.set_broker_state_known(False, reason=reconciliation.detail)

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
