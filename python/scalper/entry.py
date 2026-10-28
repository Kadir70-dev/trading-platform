"""Entry confirmation: given a direction reading, decide whether *now* is a
safe moment to act on it. Separate from direction detection so the "should I
trust this direction" question and the "should I trust this moment" question
can be reasoned about (and tested) independently.
"""
from __future__ import annotations

from .config import ScalperParams
from .types import Direction, DirectionReading, EntryDecision

ACTIONABLE = {Direction.LONG_STRONG, Direction.SHORT_STRONG}


class EntryConfirmer:
    def __init__(self, params: ScalperParams):
        self.p = params

    def confirm(self, reading: DirectionReading, last_bar_body: float, spread_points: float) -> EntryDecision:
        """`last_bar_body` is close-open of the most recent closed bar — kept as
        a scalar (not a DataFrame) so this stays O(1), cheap enough to call
        millions of times in a backtest replay."""
        if reading.direction not in ACTIONABLE:
            return EntryDecision(False, "ENTRY_SKIPPED_NOT_STRONG", f"direction={reading.direction.value}")

        if spread_points > self.p.max_spread_points:
            return EntryDecision(False, "ENTRY_REJECTED_SPREAD",
                                  f"spread={spread_points:.1f}pts > max={self.p.max_spread_points}")

        chop_floor = self.p.chop_atr_floor_pct / 100.0
        if reading.atr_ratio < chop_floor:
            return EntryDecision(False, "ENTRY_REJECTED_CHOPPY",
                                  f"atr_ratio={reading.atr_ratio:.2f} < floor={chop_floor:.2f}")

        wants_long = reading.direction == Direction.LONG_STRONG
        confirms = last_bar_body > 0 if wants_long else last_bar_body < 0
        if not confirms:
            return EntryDecision(False, "ENTRY_REJECTED_WEAK_CONFIRMATION",
                                  f"last bar body ({last_bar_body:.4f}) does not confirm {reading.direction.value}")

        return EntryDecision(True, "ENTRY_CONFIRMED", f"{reading.direction.value}: {reading.reason}")
