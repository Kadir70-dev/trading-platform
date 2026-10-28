"""Shared types for the XAUUSD discretionary scalper engine."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class Direction(str, Enum):
    LONG_STRONG = "LONG_STRONG"
    LONG_WEAK = "LONG_WEAK"
    NEUTRAL = "NEUTRAL"
    SHORT_WEAK = "SHORT_WEAK"
    SHORT_STRONG = "SHORT_STRONG"


class PositionState(str, Enum):
    FLAT = "FLAT"
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass
class DirectionReading:
    direction: Direction
    score: float              # signed, roughly -100..100, positive = bullish
    reason: str
    atr_ratio: float = 0.0    # current ATR / its own rolling median (volatility regime)
    momentum_norm: float = 0.0  # ATR-normalised short-term momentum, clipped to [-1, 1]
    structure_score: float = 0.0  # -1 bearish structure, 0 mixed, +1 bullish structure


@dataclass
class EntryDecision:
    should_enter: bool
    reason_code: str
    detail: str = ""


@dataclass
class ExitDecision:
    should_exit: bool
    reason_code: str
    detail: str = ""


@dataclass
class ReentryDecision:
    action: str                        # ENTRY_INITIAL | REENTRY_SAME_DIRECTION | REENTRY_DIRECTION_SWITCH
    direction: Optional[PositionState]
    reason_code: str
    detail: str = ""


@dataclass
class Position:
    direction: PositionState           # LONG or SHORT
    entry_price: float
    entry_time: Any
    entry_bar_index: int
    risk_unit: float                   # price distance corresponding to config.stop_distance_usd
    lot_multiplier: float = 1.0        # 1.0 for a normal primary; hedge_ratio for a hedge leg
                                        # (or a hedge promoted to primary — never re-sized upward)
    ticket: Optional[int] = None       # live-only: broker position ticket
    peak_favorable_move: float = 0.0   # best price-distance-in-our-favor seen so far (MFE, for trailing)
    worst_move: float = 0.0            # worst (most negative) price-distance seen so far (-> MAE)
    reversal_streak: int = 0           # consecutive bars the latest reading has opposed this position


@dataclass
class HedgeDecision:
    should_hedge: bool
    reason_code: str
    detail: str = ""


@dataclass
class HedgeExit:
    action: str          # HEDGE_EXIT_NONE | HEDGE_EXIT_RECOVERED | HEDGE_EXIT_REVERSAL_CONFIRMED | HEDGE_EXIT_COMBINED_LOSS
    reason_code: str
    detail: str = ""


@dataclass
class DecisionLog:
    ts: Any
    symbol: str
    state: str
    event: str
    reason_code: str
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "ts": str(self.ts), "symbol": self.symbol, "state": self.state,
            "event": self.event, "reason_code": self.reason_code, "detail": self.detail,
        }
