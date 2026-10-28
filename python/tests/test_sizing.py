"""Risk-based sizing from stop distance + contract specs (requirement 6 + the
explicit $1,000 @ 0.5% = $5 budget rule). Lot is DERIVED, never a fixed 0.02,
and actual risk never exceeds the 0.5% budget (no silent 1.5% widening)."""
from scalper.sizing import compute_lot
from scalper.symbol_spec import SymbolSpec

XAU = SymbolSpec.from_symbol_info({
    "symbol": "XAUUSDm", "point": 0.01, "digits": 2, "tick_size": 0.01,
    "tick_value": 1.0, "contract_size": 100.0,
    "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01,
})


def test_budget_is_five_dollars_for_1000_at_half_percent():
    r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=3.0, spec=XAU)
    assert r.risk_budget_usd == 5.0


def test_lot_derived_from_3R_stop_not_fixed():
    # 3R emergency stop = $3/oz -> $300 risk per lot -> 5/300 = 0.0167 -> step down to 0.01
    r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=3.0, spec=XAU)
    assert not r.rejected
    assert r.lot == 0.01
    assert r.risk_usd == 3.0 and r.risk_usd <= r.risk_budget_usd


def test_lot_scales_with_tighter_stop():
    # 1R stop = $1/oz -> $100 risk per lot -> 5/100 = 0.05 exactly, risking the full $5
    r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=1.0, spec=XAU)
    assert r.lot == 0.05 and r.risk_usd == 5.0


def test_risk_never_exceeds_budget_rounding_is_down():
    # Any stop distance: realized risk at the chosen lot must be <= budget.
    for stop in (0.5, 0.8, 1.0, 1.3, 2.0, 3.0, 4.7):
        r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=stop, spec=XAU)
        if not r.rejected:
            assert r.risk_usd <= r.risk_budget_usd + 1e-9


def test_below_min_volume_rejected():
    # Tiny equity: even 0.01 lot would exceed the budget -> refuse to trade.
    r = compute_lot(equity=50.0, risk_pct=0.005, stop_distance_usd=3.0, spec=XAU)
    assert r.rejected and r.reason == "BELOW_MIN_VOLUME" and r.lot == 0.0


def test_volume_step_respected():
    r = compute_lot(equity=100000.0, risk_pct=0.005, stop_distance_usd=3.0, spec=XAU)
    # whatever the lot, it must be a multiple of the 0.01 step
    assert abs((round(r.lot / 0.01) * 0.01) - r.lot) < 1e-9


def test_max_volume_cap():
    r = compute_lot(equity=5_000_000.0, risk_pct=0.005, stop_distance_usd=0.5, spec=XAU)
    assert r.lot <= XAU.volume_max


def test_margin_rejection():
    r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=1.0, spec=XAU,
                    free_margin=1.0, price=2350.0, leverage=2000)
    assert r.rejected and r.reason == "INSUFFICIENT_MARGIN"


def test_027_lot_is_never_produced_for_small_account():
    # The historical 0.27 lot must be impossible for a $1,000 account at 0.5%.
    for stop in (0.5, 1.0, 2.0, 3.0):
        r = compute_lot(equity=1000.0, risk_pct=0.005, stop_distance_usd=stop, spec=XAU)
        assert r.lot < 0.27
