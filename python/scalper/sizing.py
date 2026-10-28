"""Risk-based position sizing from stop distance and broker contract specs.

Replaces fixed-lot sizing with: lot = risk_budget / (per-lot $ risk at the
stop distance), then rounded DOWN to the broker volume step and clamped to
[volume_min, volume_max], with margin and minimum-lot rejection.

Key rule (per approved requirement): for $1,000 at 0.5% the risk BUDGET is $5
per trade. The returned ``risk_usd`` is the ACTUAL dollar risk at the chosen
lot and is always <= the budget (rounding is always down). The budget is never
silently widened (e.g. to a 1.5% allowance) to justify a larger lot.

STEP 1 STATUS: pure helper, NOT yet wired into broker_adapter / state machine
(that is Step 6 / P1-6). It performs no I/O and places no orders.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .symbol_spec import SymbolSpec


@dataclass(frozen=True)
class SizingResult:
    lot: float                 # 0.0 when rejected
    risk_usd: float            # actual $ at risk at `lot` over the stop distance (<= budget)
    risk_budget_usd: float     # equity * risk_pct
    rejected: bool
    reason: str


def _round_down_to_step(raw: float, step: float) -> float:
    if step <= 0:
        return raw
    # Guard against binary-float drift (e.g. 0.05/0.01 = 4.999999...).
    steps = math.floor(raw / step + 1e-9)
    return round(steps * step, 8)


def compute_lot(equity: float, risk_pct: float, stop_distance_usd: float, spec: SymbolSpec,
                *, free_margin: float | None = None, price: float | None = None,
                leverage: int | None = None) -> SizingResult:
    """Size a position so the stop-out loss does not exceed ``equity*risk_pct``.

    ``stop_distance_usd`` is the protective-stop price distance (for the scalper
    this is ``emergency_stop_r_mult * stop_distance_usd``, i.e. the real broker
    SL distance). Rounding is always DOWN, so actual risk <= budget.
    """
    budget = equity * risk_pct
    if stop_distance_usd <= 0 or equity <= 0 or risk_pct <= 0:
        return SizingResult(0.0, 0.0, budget, True, "INVALID_INPUT")

    per_lot_risk = spec.value_per_lot(stop_distance_usd)   # $ lost per 1.0 lot if stop hit
    if per_lot_risk <= 0:
        return SizingResult(0.0, 0.0, budget, True, "INVALID_SPEC")

    raw_lot = budget / per_lot_risk
    lot = _round_down_to_step(raw_lot, spec.volume_step)

    if lot < spec.volume_min:
        # Even the minimum lot would risk more than the budget — refuse to trade.
        return SizingResult(0.0, 0.0, budget, True, "BELOW_MIN_VOLUME")
    if lot > spec.volume_max:
        lot = spec.volume_max  # cap; risk_usd below will reflect the capped risk

    # Margin check (optional, only when the caller supplies the inputs).
    if free_margin is not None and price is not None and leverage:
        required_margin = lot * spec.contract_size * price / leverage
        if required_margin > free_margin:
            return SizingResult(0.0, 0.0, budget, True, "INSUFFICIENT_MARGIN")

    risk_usd = lot * per_lot_risk
    return SizingResult(round(lot, 8), round(risk_usd, 2), round(budget, 2), False, "OK")
