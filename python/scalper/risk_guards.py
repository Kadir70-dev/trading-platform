"""Independent, side-effect-free trading risk guards: cooldown / anti-
overtrading, spread filter, max daily loss, max consecutive losses, and an
emergency kill-switch. (Max loss *per trade* is enforced separately, in
exit.py's risk-stop branch, since it's a position-level price-distance check
rather than an account-level counter.)

Holds only in-memory counters — no I/O, no broker/backtest awareness — so it
is trivially unit-testable and reusable from both the backtester and the live
runner.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .config import ScalperParams


@dataclass
class GuardResult:
    ok: bool
    reason_code: str
    detail: str = ""


def _elapsed_seconds(now: Any, then: Any) -> float:
    delta = now - then
    return delta.total_seconds() if hasattr(delta, "total_seconds") else float(delta)


def _day_of(now: Any) -> Any:
    return now.date() if hasattr(now, "date") else now


class RiskGuard:
    def __init__(self, params: ScalperParams, auto_reset_daily: bool = False):
        """`auto_reset_daily` approximates an operator checking in once a day
        and clearing the kill-switch/consecutive-loss streak after reviewing
        it — realistic for scoring a strategy's edge across a multi-year
        backtest. The live runner must NEVER set this: a tripped kill-switch
        there means a real account halt that should require an actual human
        to look at it, not silently clear itself the next day.
        """
        self.p = params
        self.auto_reset_daily = auto_reset_daily
        self.daily_pnl: float = 0.0
        self.current_day: Optional[Any] = None
        self.consecutive_losses: int = 0
        self.last_trade_close_time: Any = None
        self.kill_switch: bool = False
        self.kill_switch_reason: str = ""
        self.flip_times: list = []

    def trip_kill_switch(self, reason: str) -> None:
        self.kill_switch = True
        self.kill_switch_reason = reason

    def _roll_day(self, now: Any) -> None:
        day = _day_of(now)
        if self.current_day is None:
            self.current_day = day
        elif day != self.current_day:
            self.current_day = day
            self.daily_pnl = 0.0
            if self.auto_reset_daily:
                self.kill_switch = False
                self.kill_switch_reason = ""
                self.consecutive_losses = 0

    def check_pretrade(self, spread_points: float, now: Any) -> GuardResult:
        self._roll_day(now)
        if self.kill_switch:
            return GuardResult(False, "BLOCKED_KILL_SWITCH", self.kill_switch_reason)
        if self.daily_pnl <= -self.p.max_daily_loss_usd:
            return GuardResult(False, "BLOCKED_MAX_DAILY_LOSS",
                                f"daily_pnl={self.daily_pnl:.2f} <= -{self.p.max_daily_loss_usd}")
        if self.consecutive_losses >= self.p.max_consecutive_losses:
            return GuardResult(False, "BLOCKED_MAX_CONSECUTIVE_LOSSES",
                                f"{self.consecutive_losses} >= {self.p.max_consecutive_losses}")
        if spread_points > self.p.max_spread_points:
            return GuardResult(False, "BLOCKED_SPREAD_FILTER",
                                f"spread={spread_points:.1f} > max={self.p.max_spread_points}")
        if self.last_trade_close_time is not None:
            elapsed = _elapsed_seconds(now, self.last_trade_close_time)
            if elapsed < self.p.cooldown_sec:
                return GuardResult(False, "BLOCKED_COOLDOWN",
                                    f"{elapsed:.0f}s < cooldown {self.p.cooldown_sec}s")
        return GuardResult(True, "GUARD_OK")

    def check_flip_allowed(self, now: Any) -> GuardResult:
        """Gate for the immediate close-and-flip path: a confirmed reversal is
        allowed to bypass the normal cooldown entirely (it's a deliberate
        signal, not overtrading) — but repeated rapid flips still trip this
        anti-churn ceiling, which is where a cooldown-like wait re-enters.
        """
        if self.kill_switch:
            return GuardResult(False, "BLOCKED_KILL_SWITCH", self.kill_switch_reason)
        self.flip_times = [t for t in self.flip_times if _elapsed_seconds(now, t) < 3600]
        if len(self.flip_times) >= self.p.max_flips_per_hour:
            return GuardResult(False, "BLOCKED_MAX_FLIP_FREQUENCY",
                                f"{len(self.flip_times)} flips in the last hour >= {self.p.max_flips_per_hour}")
        return GuardResult(True, "FLIP_OK")

    def register_flip(self, now: Any) -> None:
        self.flip_times.append(now)

    def register_trade_result(self, pnl: float, now: Any) -> None:
        self._roll_day(now)
        self.daily_pnl += pnl
        self.last_trade_close_time = now
        if pnl < 0:
            self.consecutive_losses += 1
        else:
            self.consecutive_losses = 0
        if self.daily_pnl <= -self.p.max_daily_loss_usd:
            self.trip_kill_switch(f"max daily loss breached ({self.daily_pnl:.2f})")
        if self.consecutive_losses >= self.p.max_consecutive_losses:
            self.trip_kill_switch(f"{self.consecutive_losses} consecutive losses")
