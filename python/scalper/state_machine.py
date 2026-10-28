"""The scalper's state machine — discretionary "let winners run, cut losers
fast and flip" behavior:

    FLAT -> detect direction -> LONG or SHORT -> monitor continuously
        -> winning: let profit run (trail), exit on reversal/fade/trail-stop
        -> losing: on the first CONFIRMED opposite-direction signal, close
           immediately and flip straight into the opposite direction
        -> back to FLAT (non-flip exits) -> immediately re-evaluate next bar

`on_bar` is the single entry point, called once per new closed bar (live) or
once per historical bar (backtest). A flip is a same-bar action (close +
reopen in one call) since the reversal that triggered the cut already tells
us the new direction — waiting a bar would defeat "close immediately". Every
other transition follows the normal next-bar FLAT re-evaluation.

Hedging (hedge.py) is NOT wired into this flow — disabled per current
guidance until the base strategy is profitable on its own. The module and its
own unit tests remain in place for later re-integration.
"""
from __future__ import annotations

from typing import Any, List

import pandas as pd

from .config import ScalperParams
from .direction import DirectionDetector
from .entry import EntryConfirmer
from .exit import ExitEvaluator
from .hedge import leg_pnl
from .reentry import ReentryGate
from .risk_guards import RiskGuard
from .types import DecisionLog, Direction, Position, PositionState


class ScalperStateMachine:
    def __init__(self, params: ScalperParams, symbol: str | None = None,
                 pnl_mode: str = "internal"):
        """pnl_mode:
        * "internal" (default) — the strategy's own ``leg_pnl`` updates the risk
          guard at close time. Used by the backtester and SimBroker replays,
          where there is no broker deal ledger. Behavior is identical to before.
        * "broker" — the strategy NEVER books its computed P&L into the guard.
          The live runner reconciles the actual broker deals and calls
          ``record_broker_realized()`` with the authoritative figure instead, so
          a simulated number can never drive real-account risk controls.
        Strategy decisions (direction/entry/flip/trailing) are identical in both
        modes; only WHO supplies the guard's realized P&L differs.
        """
        if pnl_mode not in ("internal", "broker"):
            raise ValueError(f"pnl_mode must be 'internal' or 'broker', got {pnl_mode!r}")
        self.p = params
        self.symbol = symbol or params.symbol
        self.pnl_mode = pnl_mode
        self.direction = DirectionDetector(params)
        self.entry = EntryConfirmer(params)
        self.exit = ExitEvaluator(params)
        self.guard = RiskGuard(params)
        self.reentry = ReentryGate()

        self.state = PositionState.FLAT
        self.position: Position | None = None

        self.direction_switch_count = 0
        self.same_direction_reentry_count = 0
        self.flip_count = 0
        # Closes awaiting broker reconciliation (broker mode only): the runner
        # drains these after reconciling the real deals.
        self.pending_broker_closes: list = []

    def _book_result(self, pnl: float, now: Any, *, ticket: int | None = None,
                     entry_price: float | None = None, exit_price: float | None = None) -> None:
        """Route a closed trade's realized P&L. In internal mode the strategy's
        computed ``pnl`` updates the guard immediately (unchanged behavior). In
        broker mode the guard is NOT touched here — the close is queued for the
        runner to reconcile against authoritative broker deals."""
        if self.pnl_mode == "internal":
            self.guard.register_trade_result(pnl, now)
        else:
            self.pending_broker_closes.append({
                "ticket": ticket, "strategy_pnl": pnl, "time": now,
                "entry_price": entry_price, "exit_price": exit_price,
            })

    def record_broker_realized(self, realized: float, now: Any) -> None:
        """Feed AUTHORITATIVE broker-reconciled realized P&L into the risk guard
        (broker mode). Dedup/idempotency is the caller's responsibility via the
        reconciliation ledger — this simply records a confirmed figure."""
        self.guard.register_trade_result(realized, now)

    def on_bar(self, df: pd.DataFrame, spread_points: float, now: Any) -> List[DecisionLog]:
        price = float(df.iloc[-1]["close"])
        bar_index = len(df) - 1

        if self.state == PositionState.FLAT:
            return self._on_flat(df, price, bar_index, spread_points, now)
        return self._on_position(df, price, bar_index, spread_points, now)

    def _on_flat(self, df: pd.DataFrame, price: float, bar_index: int,
                 spread_points: float, now: Any) -> List[DecisionLog]:
        gr = self.guard.check_pretrade(spread_points, now)
        if not gr.ok:
            return [self._log(now, "HOLD_FLAT", gr.reason_code, {"detail": gr.detail})]

        reading = self.direction.detect(df)
        if reading.direction not in (Direction.LONG_STRONG, Direction.SHORT_STRONG):
            return [self._log(now, "HOLD_FLAT", "DIRECTION_NOT_STRONG",
                               {"direction": reading.direction.value, "reason": reading.reason})]

        last = df.iloc[-1]
        last_bar_body = float(last["close"]) - float(last["open"])
        ed = self.entry.confirm(reading, last_bar_body, spread_points)
        if not ed.should_enter:
            return [self._log(now, "HOLD_FLAT", ed.reason_code, {"detail": ed.detail})]

        return [self._enter(reading, price, bar_index, now, ed.reason_code, ed.detail)]

    def _enter(self, reading, price: float, bar_index: int, now: Any,
               reason_code: str, detail: str) -> DecisionLog:
        new_state = (PositionState.LONG if reading.direction == Direction.LONG_STRONG
                     else PositionState.SHORT)
        self.position = Position(new_state, price, now, bar_index, self.p.stop_distance_usd)
        self.state = new_state

        rd = self.reentry.classify_entry(new_state)
        if rd.action == "REENTRY_SAME_DIRECTION":
            self.same_direction_reentry_count += 1
        elif rd.action == "REENTRY_DIRECTION_SWITCH":
            self.direction_switch_count += 1

        return self._log(now, f"ENTER_{new_state.value}", reason_code,
                          {"detail": detail, "price": price, "risk_unit": self.position.risk_unit,
                           "reentry": rd.action, "reentry_detail": rd.detail})

    def _on_position(self, df: pd.DataFrame, price: float, bar_index: int,
                      spread_points: float, now: Any) -> List[DecisionLog]:
        assert self.position is not None
        reading = self.direction.detect(df)
        xd = self.exit.evaluate(self.position, price, reading, reading.momentum_norm, bar_index)

        bars_held = bar_index - self.position.entry_bar_index
        if not xd.should_exit:
            return [self._log(now, f"MONITOR_{self.state.value}", "MONITOR_HOLDING",
                               {"price": price, "bars_held": bars_held,
                                "peak_favorable_move": round(self.position.peak_favorable_move, 4),
                                "reversal_streak": self.position.reversal_streak})]

        if xd.reason_code == "EXIT_REVERSAL_CONFIRMED_FLIP":
            return self._close_and_flip(df, price, bar_index, spread_points, now, xd.reason_code, xd.detail)

        return [self._close_primary_only(price, bar_index, now, xd.reason_code, xd.detail)]

    def _close_and_flip(self, df: pd.DataFrame, price: float, bar_index: int,
                         spread_points: float, now: Any, reason_code: str, detail: str) -> List[DecisionLog]:
        old_position = self.position
        old_direction = self.state
        assert old_position is not None
        pnl = leg_pnl(old_position, price, self.p)
        bars_held = bar_index - old_position.entry_bar_index

        self._book_result(pnl, now, ticket=old_position.ticket,
                          entry_price=old_position.entry_price, exit_price=price)
        self.reentry.record_exit(old_direction, now)
        self.state = PositionState.FLAT
        self.position = None

        close_log = self._log(now, f"EXIT_{old_direction.value}", reason_code,
                              {"detail": detail, "entry_price": old_position.entry_price,
                               "exit_price": price, "pnl": round(pnl, 4), "bars_held": bars_held})

        # A confirmed reversal bypasses the ordinary per-trade cooldown (it's
        # a deliberate signal, not overtrading) — only the flip-frequency
        # ceiling and a fresh spread/entry-quality check gate the flip.
        fg = self.guard.check_flip_allowed(now)
        if not fg.ok:
            return [close_log, self._log(now, "HOLD_FLAT", fg.reason_code, {"detail": fg.detail})]

        new_direction = PositionState.SHORT if old_direction == PositionState.LONG else PositionState.LONG
        expected = Direction.SHORT_STRONG if new_direction == PositionState.SHORT else Direction.LONG_STRONG
        reading = self.direction.detect(df)
        if reading.direction != expected:
            return [close_log, self._log(now, "HOLD_FLAT", "FLIP_SKIPPED_SIGNAL_FADED",
                                         {"direction": reading.direction.value})]

        last = df.iloc[-1]
        last_bar_body = float(last["close"]) - float(last["open"])
        ed = self.entry.confirm(reading, last_bar_body, spread_points)
        if not ed.should_enter:
            return [close_log, self._log(now, "HOLD_FLAT", ed.reason_code,
                                         {"detail": ed.detail + " (flip skipped)"})]

        self.position = Position(new_direction, price, now, bar_index, self.p.stop_distance_usd)
        self.state = new_direction
        self.guard.register_flip(now)
        self.direction_switch_count += 1
        self.flip_count += 1

        flip_log = self._log(now, f"FLIP_{old_direction.value}_TO_{new_direction.value}", reason_code,
                             {"old_direction": old_direction.value, "old_entry": old_position.entry_price,
                              "close_price": price, "realized_pnl": round(pnl, 4),
                              "reversal_reason": detail, "new_direction": new_direction.value,
                              "new_entry": price})
        return [close_log, flip_log]

    def _close_primary_only(self, price: float, bar_index: int, now: Any,
                             reason_code: str, detail: str) -> DecisionLog:
        assert self.position is not None
        pnl = leg_pnl(self.position, price, self.p)
        exited_direction = self.state
        bars_held = bar_index - self.position.entry_bar_index
        self._book_result(pnl, now, ticket=self.position.ticket,
                          entry_price=self.position.entry_price, exit_price=price)
        self.reentry.record_exit(exited_direction, now)

        log = self._log(now, f"EXIT_{exited_direction.value}", reason_code,
                        {"detail": detail, "entry_price": self.position.entry_price,
                         "exit_price": price, "pnl": round(pnl, 4), "bars_held": bars_held})
        self.state = PositionState.FLAT
        self.position = None
        return log

    def _log(self, now: Any, event: str, reason_code: str, detail: dict) -> DecisionLog:
        return DecisionLog(now, self.symbol, self.state.value, event, reason_code, detail)
