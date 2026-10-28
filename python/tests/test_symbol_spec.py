"""SymbolSpec conversions — tick/point and per-lot value (requirement 6)."""
from scalper.symbol_spec import SymbolSpec

# A realistic XAUUSDm spec: point 0.001 (the live10 pre-flight implied this),
# tick_value = contract_size * tick_size = 100 * 0.001 = 0.1 per 0.001 move.
XAU_M = {
    "symbol": "XAUUSDm", "point": 0.001, "digits": 3, "tick_size": 0.001,
    "tick_value": 0.1, "contract_size": 100.0,
    "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01,
}
EUR = {
    "symbol": "EURUSD", "point": 0.00001, "digits": 5, "tick_size": 0.00001,
    "tick_value": 1.0, "contract_size": 100000.0,
    "volume_min": 0.01, "volume_max": 100.0, "volume_step": 0.01,
}


def test_from_symbol_info_parses():
    s = SymbolSpec.from_symbol_info(XAU_M)
    assert s.contract_size == 100.0 and s.point == 0.001 and s.volume_step == 0.01


def test_value_per_lot_xau_is_100_per_dollar():
    s = SymbolSpec.from_symbol_info(XAU_M)
    assert abs(s.value_per_lot(1.0) - 100.0) < 1e-9     # $1/oz on 1 lot = $100
    assert abs(s.value_per_lot(3.0) - 300.0) < 1e-9     # 3R ($3/oz) = $300


def test_points_conversion_uses_broker_point():
    s = SymbolSpec.from_symbol_info(XAU_M)
    # $0.26 spread at point 0.001 => 260 points (matches the live10 pre-flight)
    assert abs(s.to_points(0.26) - 260.0) < 1e-6
    assert abs(s.from_points(60) - 0.06) < 1e-9


def test_value_per_lot_fx():
    s = SymbolSpec.from_symbol_info(EUR)
    assert abs(s.value_per_lot(0.0010) - 100.0) < 1e-6  # 10 pip move on 1 lot = $100
