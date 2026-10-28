"""Market-session awareness for XAUUSD/Gold.

The VPS stays online 24/7, but new entries are only permitted during valid
trading sessions. This is a CONSERVATIVE gate on top of the broker's own
`trade_allowed` flag — it blocks weekends and the daily maintenance break so the
bot never attempts orders at rollover. It does NOT affect any strategy decision;
it only decides whether an already-signalled entry may be executed now.

Times are UTC. Defaults approximate Exness metals hours; all boundaries are
configurable (and the broker's `trade_allowed` remains the final authority in
the runner). This module is pure and fully offline-testable.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone


@dataclass(frozen=True)
class MarketSchedule:
    # Weekly open: Sunday at this UTC time. Weekly close: Friday at this UTC time.
    weekly_open_weekday: int = 6          # Monday=0 .. Sunday=6
    weekly_open_time: time = time(22, 0)  # Sun 22:00 UTC
    weekly_close_weekday: int = 4         # Friday
    weekly_close_time: time = time(21, 0)  # Fri 21:00 UTC
    # Daily maintenance break (applies Mon–Thu): [start, end) UTC.
    daily_break_start: time = time(21, 0)
    daily_break_end: time = time(22, 0)

    def is_open(self, dt: datetime) -> bool:
        """True if Gold trades at `dt` (tz-aware; converted to UTC)."""
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(timezone.utc)
        wd, t = dt.weekday(), dt.time()

        # Weekend: Saturday always closed; Sunday closed until weekly open;
        # Friday closed after weekly close.
        if wd == 5:                                   # Saturday
            return False
        if wd == self.weekly_open_weekday:            # Sunday
            return t >= self.weekly_open_time
        if wd == self.weekly_close_weekday:           # Friday
            if t >= self.weekly_close_time:
                return False
            return not self._in_break(t)
        # Monday–Thursday: open except during the daily maintenance break.
        return not self._in_break(t)

    def _in_break(self, t: time) -> bool:
        if self.daily_break_start == self.daily_break_end:
            return False
        if self.daily_break_start < self.daily_break_end:
            return self.daily_break_start <= t < self.daily_break_end
        # wraps midnight
        return t >= self.daily_break_start or t < self.daily_break_end


DEFAULT_SCHEDULE = MarketSchedule()


def entries_allowed(dt: datetime, schedule: MarketSchedule | None = None) -> bool:
    return (schedule or DEFAULT_SCHEDULE).is_open(dt)
