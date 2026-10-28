"""Hedging overlay: a separate, opt-in risk-management layer on top of the
primary scalper position — NOT martingale or loss-chasing.

Rules enforced here: the hedge never changes the primary's size, never
averages down, opens opposite-direction at a fixed configured ratio (25% /
50% / 100%) of the primary's fixed lot, and at most one hedge layer may be
active at a time. It only exists to reduce net directional exposure and buy
time to reassess when price moves adversely against the primary AND a genuine
reversal or volatility spike confirms it — never on ordinary drawdown alone.

All exit decisions here are based on COMBINED (primary + hedge) P&L, per the
requirement that hedge risk decisions must never look at one leg in isolation.
"""
from __future__ import annotations

from .config import ScalperParams
from .types import Direction, DirectionReading, HedgeDecision, HedgeExit, Position, PositionState


def leg_pnl(position: Position, price: float, params: ScalperParams) -> float:
    diff = price - position.entry_price
    if position.direction == PositionState.SHORT:
        diff = -diff
    return diff * params.lot_size * position.lot_multiplier * params.contract_size


def combined_pnl(primary: Position, hedge: Position | None, price: float, params: ScalperParams) -> float:
    total = leg_pnl(primary, price, params)
    if hedge is not None:
        total += leg_pnl(hedge, price, params)
    return total


class HedgeManager:
    def __init__(self, params: ScalperParams):
        self.p = params

    def evaluate_trigger(self, primary: Position, price: float, reading: DirectionReading,
                          has_hedge: bool) -> HedgeDecision:
        if not self.p.hedge_enabled:
            return HedgeDecision(False, "HEDGE_DISABLED")
        if has_hedge or self.p.hedge_max_layers < 1:
            return HedgeDecision(False, "HEDGE_SKIPPED_MAX_LAYERS", "one hedge layer already active")

        diff = price - primary.entry_price
        adverse = -diff if primary.direction == PositionState.LONG else diff
        if adverse < self.p.hedge_trigger_adverse_usd:
            return HedgeDecision(False, "HEDGE_SKIPPED_INSUFFICIENT_ADVERSE_MOVE",
                                  f"adverse={adverse:.4f} < trigger={self.p.hedge_trigger_adverse_usd}")

        opposite_strong = (Direction.SHORT_STRONG if primary.direction == PositionState.LONG
                           else Direction.LONG_STRONG)
        reversal_confirmed = reading.direction == opposite_strong
        volatility_spike = reading.atr_ratio >= self.p.hedge_volatility_spike_ratio
        if not (reversal_confirmed or volatility_spike):
            return HedgeDecision(False, "HEDGE_SKIPPED_NO_CONFIRMATION",
                                  f"direction={reading.direction.value} atr_ratio={reading.atr_ratio:.2f}")

        trigger = "REVERSAL" if reversal_confirmed else "VOLATILITY_SPIKE"
        return HedgeDecision(True, f"HEDGE_TRIGGERED_{trigger}",
                              f"adverse={adverse:.4f} atr_ratio={reading.atr_ratio:.2f} "
                              f"ratio={self.p.hedge_ratio}")

    def evaluate_exit(self, primary: Position, hedge: Position, price: float,
                       reading: DirectionReading) -> HedgeExit:
        combined = combined_pnl(primary, hedge, price, self.p)

        if combined <= -self.p.hedge_combined_loss_usd:
            return HedgeExit("HEDGE_EXIT_COMBINED_LOSS", "HEDGE_EXIT_COMBINED_LOSS",
                              f"combined_pnl={combined:.2f} <= -{self.p.hedge_combined_loss_usd}")

        original_strong = (Direction.LONG_STRONG if primary.direction == PositionState.LONG
                           else Direction.SHORT_STRONG)
        if reading.direction == original_strong:
            return HedgeExit("HEDGE_EXIT_RECOVERED", "HEDGE_EXIT_RECOVERED",
                              f"original direction {primary.direction.value} confirmed strong again; "
                              f"combined_pnl={combined:.2f}")

        hedge_strong = (Direction.LONG_STRONG if hedge.direction == PositionState.LONG
                       else Direction.SHORT_STRONG)
        if reading.direction == hedge_strong:
            return HedgeExit("HEDGE_EXIT_REVERSAL_CONFIRMED", "HEDGE_EXIT_REVERSAL_CONFIRMED",
                              f"hedge direction {hedge.direction.value} confirmed strong — reversal real; "
                              f"combined_pnl={combined:.2f}")

        return HedgeExit("HEDGE_EXIT_NONE", "HEDGE_MONITOR", f"combined_pnl={combined:.2f}")
