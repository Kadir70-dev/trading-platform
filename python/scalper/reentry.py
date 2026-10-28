"""Post-exit re-evaluation bookkeeping: classifies a new entry (once the state
machine's own direction/entry checks confirm one) as either a same-direction
re-entry or a direction switch relative to the position just closed. This is
exactly what feeds the "direction switches" / "same-direction re-entries"
backtest statistics — it does not re-run detection itself.
"""
from __future__ import annotations

from typing import Any, Optional

from .types import PositionState, ReentryDecision


class ReentryGate:
    def __init__(self) -> None:
        self.last_exit_direction: Optional[PositionState] = None
        self.last_exit_time: Any = None

    def record_exit(self, direction: PositionState, when: Any) -> None:
        self.last_exit_direction = direction
        self.last_exit_time = when

    def classify_entry(self, new_direction: PositionState) -> ReentryDecision:
        if self.last_exit_direction is None:
            return ReentryDecision("ENTRY_INITIAL", new_direction, "ENTRY_INITIAL",
                                    "first entry of the session")
        if new_direction == self.last_exit_direction:
            return ReentryDecision("REENTRY_SAME_DIRECTION", new_direction, "REENTRY_SAME_DIRECTION",
                                    f"re-entered {new_direction.value} after prior "
                                    f"{self.last_exit_direction.value} exit")
        return ReentryDecision("REENTRY_DIRECTION_SWITCH", new_direction, "REENTRY_DIRECTION_SWITCH",
                                f"switched to {new_direction.value} from prior "
                                f"{self.last_exit_direction.value}")
