"""HedgeManager tests: trigger conditions, exit priority, combined P&L."""
from scalper.config import ScalperParams
from scalper.hedge import HedgeManager, combined_pnl, leg_pnl
from scalper.types import Direction, DirectionReading, Position, PositionState


def _params(**overrides):
    p = ScalperParams(_env_file=None)
    p.hedge_enabled = True
    p.hedge_ratio = 0.5
    p.hedge_trigger_adverse_usd = 0.5
    p.hedge_volatility_spike_ratio = 1.5
    p.hedge_combined_loss_usd = 10.0
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def _long(entry=100.0):
    return Position(PositionState.LONG, entry, 0, 0, risk_unit=1.0)


def test_hedge_disabled_never_triggers():
    p = _params(hedge_enabled=False)
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "", atr_ratio=2.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.0, reading=reading, has_hedge=False)
    assert not d.should_hedge and d.reason_code == "HEDGE_DISABLED"


def test_hedge_skipped_when_layer_already_active():
    p = _params()
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "", atr_ratio=2.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.0, reading=reading, has_hedge=True)
    assert not d.should_hedge and d.reason_code == "HEDGE_SKIPPED_MAX_LAYERS"


def test_hedge_skipped_insufficient_adverse_move():
    p = _params()
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "", atr_ratio=2.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.8, reading=reading, has_hedge=False)
    assert not d.should_hedge and d.reason_code == "HEDGE_SKIPPED_INSUFFICIENT_ADVERSE_MOVE"


def test_hedge_skipped_without_reversal_or_volatility_confirmation():
    p = _params()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", atr_ratio=1.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.0, reading=reading, has_hedge=False)
    assert not d.should_hedge and d.reason_code == "HEDGE_SKIPPED_NO_CONFIRMATION"


def test_hedge_triggers_on_reversal():
    p = _params()
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "", atr_ratio=1.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.0, reading=reading, has_hedge=False)
    assert d.should_hedge and d.reason_code == "HEDGE_TRIGGERED_REVERSAL"


def test_hedge_triggers_on_volatility_spike_without_reversal():
    p = _params()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", atr_ratio=2.0)
    d = HedgeManager(p).evaluate_trigger(_long(), price=99.0, reading=reading, has_hedge=False)
    assert d.should_hedge and d.reason_code == "HEDGE_TRIGGERED_VOLATILITY_SPIKE"


def test_hedge_exit_combined_loss_has_priority():
    p = _params(hedge_combined_loss_usd=1.0)
    primary = _long(100.0)
    hedge = Position(PositionState.SHORT, 99.0, 0, 0, risk_unit=1.0, lot_multiplier=0.5)
    # price falls further: primary loses big, hedge (short) gains a bit, net still a big loss
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "")  # would also match REVERSAL_CONFIRMED
    x = HedgeManager(p).evaluate_exit(primary, hedge, price=90.0, reading=reading)
    assert x.action == "HEDGE_EXIT_COMBINED_LOSS"


def test_hedge_exit_recovered_when_original_direction_confirms():
    p = _params(hedge_combined_loss_usd=1000.0)
    primary = _long(100.0)
    hedge = Position(PositionState.SHORT, 99.0, 0, 0, risk_unit=1.0, lot_multiplier=0.5)
    reading = DirectionReading(Direction.LONG_STRONG, 90.0, "")
    x = HedgeManager(p).evaluate_exit(primary, hedge, price=100.5, reading=reading)
    assert x.action == "HEDGE_EXIT_RECOVERED"


def test_hedge_exit_reversal_confirmed_when_hedge_direction_confirms():
    p = _params(hedge_combined_loss_usd=1000.0)
    primary = _long(100.0)
    hedge = Position(PositionState.SHORT, 99.0, 0, 0, risk_unit=1.0, lot_multiplier=0.5)
    reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "")
    x = HedgeManager(p).evaluate_exit(primary, hedge, price=98.0, reading=reading)
    assert x.action == "HEDGE_EXIT_REVERSAL_CONFIRMED"


def test_hedge_exit_none_when_ambiguous():
    p = _params(hedge_combined_loss_usd=1000.0)
    primary = _long(100.0)
    hedge = Position(PositionState.SHORT, 99.0, 0, 0, risk_unit=1.0, lot_multiplier=0.5)
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "")
    x = HedgeManager(p).evaluate_exit(primary, hedge, price=99.5, reading=reading)
    assert x.action == "HEDGE_EXIT_NONE"


def test_combined_pnl_sums_both_legs():
    p = _params()
    primary = _long(100.0)
    hedge = Position(PositionState.SHORT, 100.0, 0, 0, risk_unit=1.0, lot_multiplier=0.5)
    total = combined_pnl(primary, hedge, price=101.0, params=p)
    expected = leg_pnl(primary, 101.0, p) + leg_pnl(hedge, 101.0, p)
    assert total == expected
    # primary (full lot, long) gains more than the half-size short hedge loses
    assert total > 0
