"""EntryConfirmer / ExitEvaluator tests.

ExitEvaluator.evaluate() mutates the Position it's given (peak_favorable_move,
worst_move, reversal_streak track running state across bars) — tests that
need multiple confirmed bars call evaluate() repeatedly on the same Position,
tests of a single condition use a fresh Position each time.
"""
from scalper.config import ScalperParams
from scalper.entry import EntryConfirmer
from scalper.exit import ExitEvaluator
from scalper.types import Direction, DirectionReading, Position, PositionState


def _params(**overrides):
    p = ScalperParams(_env_file=None)
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def test_entry_rejects_non_strong_direction():
    reading = DirectionReading(Direction.LONG_WEAK, 30.0, "weak", atr_ratio=1.0)
    d = EntryConfirmer(_params()).confirm(reading, last_bar_body=0.1, spread_points=1.0)
    assert not d.should_enter and d.reason_code == "ENTRY_SKIPPED_NOT_STRONG"


def test_entry_rejects_wide_spread():
    reading = DirectionReading(Direction.LONG_STRONG, 80.0, "strong", atr_ratio=1.0)
    d = EntryConfirmer(_params(max_spread_points=10.0)).confirm(reading, last_bar_body=0.5, spread_points=20.0)
    assert not d.should_enter and d.reason_code == "ENTRY_REJECTED_SPREAD"


def test_entry_rejects_choppy_volatility():
    reading = DirectionReading(Direction.LONG_STRONG, 80.0, "strong", atr_ratio=0.1)
    d = EntryConfirmer(_params(chop_atr_floor_pct=40.0)).confirm(reading, last_bar_body=0.5, spread_points=1.0)
    assert not d.should_enter and d.reason_code == "ENTRY_REJECTED_CHOPPY"


def test_entry_rejects_weak_last_bar_confirmation():
    reading = DirectionReading(Direction.LONG_STRONG, 80.0, "strong", atr_ratio=1.0)
    d = EntryConfirmer(_params()).confirm(reading, last_bar_body=-0.1, spread_points=1.0)
    assert not d.should_enter and d.reason_code == "ENTRY_REJECTED_WEAK_CONFIRMATION"


def test_entry_confirms_when_all_pass():
    reading = DirectionReading(Direction.SHORT_STRONG, -80.0, "strong short", atr_ratio=1.0)
    d = EntryConfirmer(_params()).confirm(reading, last_bar_body=-0.4, spread_points=1.0)
    assert d.should_enter and d.reason_code == "ENTRY_CONFIRMED"


def _long(entry=100.0, risk_unit=1.0):
    return Position(PositionState.LONG, entry, 0, 0, risk_unit=risk_unit)


def test_exit_emergency_stop_has_priority():
    p = _params(emergency_stop_r_mult=3.0, min_reversal_confirm_bars=1)
    pos = _long()
    reading = DirectionReading(Direction.LONG_STRONG, 90.0, "", momentum_norm=0.95)
    d = ExitEvaluator(p).evaluate(pos, price=96.5, reading=reading, momentum_norm=0.95, bar_index=1)
    assert d.should_exit and d.reason_code == "EXIT_EMERGENCY_STOP"


def test_small_profit_alone_does_not_close():
    p = _params(momentum_fade_threshold=-1.0)  # keep fade out of the way
    pos = _long()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.5)
    d = ExitEvaluator(p).evaluate(pos, price=100.1, reading=reading, momentum_norm=0.5, bar_index=1)
    assert not d.should_exit and d.reason_code == "EXIT_NONE_LETTING_PROFIT_RUN"


def test_profit_trail_stop_after_giveback_from_peak():
    p = _params(trail_activate_r=1.0, trail_giveback_pct=0.4, momentum_fade_threshold=-1.0,
                min_reversal_confirm_bars=99)
    ev = ExitEvaluator(p)
    pos = _long()
    d1 = ev.evaluate(pos, price=101.5, reading=DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.5),
                     momentum_norm=0.5, bar_index=1)
    assert not d1.should_exit  # peak just set, no giveback yet
    d2 = ev.evaluate(pos, price=100.85, reading=DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.5),
                     momentum_norm=0.5, bar_index=2)
    assert d2.should_exit and d2.reason_code == "EXIT_PROFIT_TRAIL_STOP"


def test_reversal_confirmed_while_winning_needs_min_bars():
    p = _params(min_reversal_confirm_bars=2, trail_activate_r=100.0,  # keep trailing out of the way
                momentum_fade_threshold=-1.0)  # keep fade out of the way too
    ev = ExitEvaluator(p)
    pos = _long()
    opposing = DirectionReading(Direction.SHORT_STRONG, -90.0, "", momentum_norm=-0.9)
    d1 = ev.evaluate(pos, price=100.3, reading=opposing, momentum_norm=-0.9, bar_index=1)
    assert not d1.should_exit, "a single opposing bar must not flip the bot"
    d2 = ev.evaluate(pos, price=100.3, reading=opposing, momentum_norm=-0.9, bar_index=2)
    assert d2.should_exit and d2.reason_code == "EXIT_REVERSAL_DETECTED"


def test_momentum_fade_while_winning():
    p = _params(momentum_fade_threshold=0.3, trail_activate_r=100.0, min_reversal_confirm_bars=99)
    pos = _long()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.1)
    d = ExitEvaluator(p).evaluate(pos, price=100.3, reading=reading, momentum_norm=0.1, bar_index=1)
    assert d.should_exit and d.reason_code == "EXIT_MOMENTUM_FADE"


def test_losing_position_awaits_confirmation_before_flipping():
    p = _params(min_reversal_confirm_bars=2, max_hold_bars=1000)
    ev = ExitEvaluator(p)
    pos = _long()
    opposing = DirectionReading(Direction.SHORT_STRONG, -90.0, "", momentum_norm=-0.9)
    d1 = ev.evaluate(pos, price=99.7, reading=opposing, momentum_norm=-0.9, bar_index=1)
    assert not d1.should_exit and d1.reason_code == "EXIT_NONE_AWAITING_CONFIRMATION"
    d2 = ev.evaluate(pos, price=99.7, reading=opposing, momentum_norm=-0.9, bar_index=2)
    assert d2.should_exit and d2.reason_code == "EXIT_REVERSAL_CONFIRMED_FLIP"


def test_losing_position_does_not_wait_for_a_wide_stop():
    """The whole point of the redesign: a loser is cut on confirmation, well
    before the old wide risk_unit-based stop would ever have fired."""
    p = _params(min_reversal_confirm_bars=1, emergency_stop_r_mult=3.0)
    pos = _long(risk_unit=1.0)
    opposing = DirectionReading(Direction.SHORT_STRONG, -90.0, "", momentum_norm=-0.9)
    # Adverse move of only 0.2 — nowhere near the 3.0 emergency backstop.
    d = ExitEvaluator(p).evaluate(pos, price=99.8, reading=opposing, momentum_norm=-0.9, bar_index=1)
    assert d.should_exit and d.reason_code == "EXIT_REVERSAL_CONFIRMED_FLIP"


def test_max_hold_time_while_losing_without_confirmation():
    p = _params(min_reversal_confirm_bars=99, max_hold_bars=3)
    pos = _long()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.0)
    d = ExitEvaluator(p).evaluate(pos, price=99.9, reading=reading, momentum_norm=0.0, bar_index=5)
    assert d.should_exit and d.reason_code == "EXIT_MAX_HOLD_TIME"


def test_exit_none_when_losing_and_unconfirmed():
    p = _params(min_reversal_confirm_bars=99, max_hold_bars=1000)
    pos = _long()
    reading = DirectionReading(Direction.NEUTRAL, 0.0, "", momentum_norm=0.0)
    d = ExitEvaluator(p).evaluate(pos, price=99.9, reading=reading, momentum_norm=0.0, bar_index=1)
    assert not d.should_exit and d.reason_code == "EXIT_NONE_AWAITING_CONFIRMATION"
