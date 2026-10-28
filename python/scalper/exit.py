"""Exit logic — asymmetric, discretionary "let winners run / cut losers fast"
behavior, replacing the old fixed quick-target/quick-stop model.

Winning positions: let profit run while structure/momentum still supports the
direction; a trailing stop only arms once favorable move clears
`trail_activate_r * risk_unit`, then exits on giveback from the peak, a
confirmed reversal, or a hard momentum fade. Small profit alone never closes
the trade.

Losing (or breakeven) positions: do NOT wait for a wide stop. The primary
loss-cut trigger is the first *confirmed* opposite-direction signal —
"confirmed" meaning it has held for `min_reversal_confirm_bars` consecutive
bars, so a single noisy tick can't flip the bot (this is the state machine's
signal to close-and-flip, see state_machine.py).

A hard catastrophic backstop (`emergency_stop_r_mult * risk_unit`) is always
active regardless of P&L sign — it exists purely for gap/spike risk, mirrored
as a real broker-side SL by broker_adapter.py in live trading, not as the
strategy's normal exit path.
"""
from __future__ import annotations

from .config import ScalperParams
from .types import Direction, DirectionReading, ExitDecision, Position, PositionState


def favorable_move(position: Position, price: float) -> float:
    diff = price - position.entry_price
    return diff if position.direction == PositionState.LONG else -diff


def _opposite_strong(direction: PositionState) -> Direction:
    return Direction.SHORT_STRONG if direction == PositionState.LONG else Direction.LONG_STRONG


class ExitEvaluator:
    def __init__(self, params: ScalperParams):
        self.p = params

    def evaluate(self, position: Position, price: float, reading: DirectionReading,
                 momentum_norm: float, bar_index: int) -> ExitDecision:
        move = favorable_move(position, price)
        position.peak_favorable_move = max(position.peak_favorable_move, move)
        position.worst_move = min(position.worst_move, move)

        opposing = reading.direction == _opposite_strong(position.direction)
        position.reversal_streak = position.reversal_streak + 1 if opposing else 0

        if move <= -position.risk_unit * self.p.emergency_stop_r_mult:
            return ExitDecision(True, "EXIT_EMERGENCY_STOP",
                                 f"adverse move {move:.4f} breached catastrophic backstop "
                                 f"(-{position.risk_unit * self.p.emergency_stop_r_mult:.4f})")

        bars_held = bar_index - position.entry_bar_index

        if move > 0:
            return self._evaluate_winning(position, move, momentum_norm, bars_held)
        return self._evaluate_losing(position, bars_held)

    def _evaluate_winning(self, position: Position, move: float, momentum_norm: float,
                           bars_held: int) -> ExitDecision:
        if position.peak_favorable_move >= self.p.trail_activate_r * position.risk_unit:
            giveback = position.peak_favorable_move - move
            trail_trigger = position.peak_favorable_move * self.p.trail_giveback_pct
            if giveback >= trail_trigger:
                return ExitDecision(True, "EXIT_PROFIT_TRAIL_STOP",
                                     f"peak={position.peak_favorable_move:.4f} giveback={giveback:.4f} "
                                     f">= {trail_trigger:.4f}")

        # Confirmed reversal (N bars of a fresh opposite-STRONG reading) beats
        # a soft momentum fade — it's the stronger, more specific signal.
        if position.reversal_streak >= self.p.min_reversal_confirm_bars:
            return ExitDecision(True, "EXIT_REVERSAL_DETECTED",
                                 f"{position.reversal_streak} confirmed opposite-direction bars while winning")

        fading = (momentum_norm < self.p.momentum_fade_threshold if position.direction == PositionState.LONG
                  else momentum_norm > -self.p.momentum_fade_threshold)
        if fading:
            return ExitDecision(True, "EXIT_MOMENTUM_FADE",
                                 f"momentum_norm={momentum_norm:.2f} weakening against {position.direction.value}")

        if bars_held >= self.p.max_hold_bars:
            return ExitDecision(True, "EXIT_MAX_HOLD_TIME", f"bars_held={bars_held}")

        return ExitDecision(False, "EXIT_NONE_LETTING_PROFIT_RUN")

    def _evaluate_losing(self, position: Position, bars_held: int) -> ExitDecision:
        if position.reversal_streak >= self.p.min_reversal_confirm_bars:
            return ExitDecision(True, "EXIT_REVERSAL_CONFIRMED_FLIP",
                                 f"{position.reversal_streak} confirmed opposite-direction bars while losing "
                                 "— close and flip")

        if bars_held >= self.p.max_hold_bars:
            return ExitDecision(True, "EXIT_MAX_HOLD_TIME", f"bars_held={bars_held}")

        return ExitDecision(False, "EXIT_NONE_AWAITING_CONFIRMATION")
