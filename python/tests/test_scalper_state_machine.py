"""Integration tests for the discretionary let-winners-run/cut-losers-fast
state machine, including the immediate close-and-flip path.
"""
import numpy as np
import pandas as pd
import pytest

from scalper.config import ScalperParams
from scalper.state_machine import ScalperStateMachine
from scalper.types import Direction, DirectionReading, PositionState


def _wiggle_trend(n, start, step, amplitude=1.5, period=10):
    i = np.arange(n)
    close = start + i * step + amplitude * np.sin(2 * np.pi * i / period)
    return pd.DataFrame({
        "open": close - 0.05, "high": close + 0.3, "low": close - 0.3, "close": close,
    })


def _path():
    up = _wiggle_trend(150, 2000.0, step=0.4)
    down = _wiggle_trend(150, float(up["close"].iloc[-1]), step=-0.4)
    return pd.concat([up, down], ignore_index=True)


def _fast_params():
    p = ScalperParams(_env_file=None)
    p.stop_distance_usd = 0.5
    p.lot_size = 0.01
    p.contract_size = 100.0
    p.cooldown_sec = 0
    p.max_spread_points = 100.0
    return p


@pytest.fixture()
def replay_logs():
    df = _path()
    sm = ScalperStateMachine(_fast_params(), symbol="XAUUSD")
    all_logs = []
    for i in range(30, len(df)):
        all_logs.extend(sm.on_bar(df.iloc[:i + 1], spread_points=1.0, now=i))
    return sm, all_logs


def test_state_machine_enters_and_exits(replay_logs):
    sm, logs = replay_logs
    events = [l.event for l in logs]
    assert any(e.startswith("ENTER_") for e in events)
    assert any(e.startswith("EXIT_") for e in events)


def test_state_machine_ends_flat_or_in_position(replay_logs):
    sm, _ = replay_logs
    assert sm.state in (PositionState.FLAT, PositionState.LONG, PositionState.SHORT)


def test_reentry_or_switch_classified_after_first_exit(replay_logs):
    sm, logs = replay_logs
    enters = [l for l in logs if l.event.startswith("ENTER_")]
    assert len(enters) >= 1
    reentry_actions = {l.detail.get("reentry") for l in enters}
    assert reentry_actions <= {"ENTRY_INITIAL", "REENTRY_SAME_DIRECTION", "REENTRY_DIRECTION_SWITCH"}
    if len(enters) > 1:
        assert reentry_actions - {"ENTRY_INITIAL"}


def test_every_decision_has_a_reason_code(replay_logs):
    _, logs = replay_logs
    assert all(l.reason_code for l in logs)


def _flip_params():
    p = ScalperParams(_env_file=None)
    p.stop_distance_usd = 1.0
    p.lot_size = 0.01
    p.contract_size = 100.0
    p.min_reversal_confirm_bars = 2
    p.momentum_fade_threshold = -1.0  # keep the softer fade signal out of the way
    p.trail_activate_r = 100.0        # keep trailing out of the way
    p.max_hold_bars = 1000
    p.cooldown_sec = 999999           # prove the flip bypasses this, not that it's zero
    p.max_spread_points = 100.0
    p.max_flips_per_hour = 6
    return p


def _flat_df(n=40, price=2000.0, bullish_body=True):
    close = np.full(n, price)
    open_ = close - 0.01 if bullish_body else close + 0.01
    return pd.DataFrame({"open": open_, "high": close + 0.05, "low": close - 0.05, "close": close})


def test_losing_position_flips_immediately_on_confirmed_reversal_bypassing_cooldown():
    p = _flip_params()
    sm = ScalperStateMachine(p, symbol="XAUUSD")

    entry_reading = DirectionReading(Direction.LONG_STRONG, 90.0, "entry", atr_ratio=1.0, momentum_norm=0.9)
    adverse_reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "adverse", atr_ratio=1.0, momentum_norm=-0.9)

    sm.direction.detect = lambda _df: entry_reading
    sm.on_bar(_flat_df(price=2000.0), spread_points=1.0, now=0)
    assert sm.state == PositionState.LONG

    sm.direction.detect = lambda _df: adverse_reading
    logs1 = sm.on_bar(_flat_df(price=1999.8), spread_points=1.0, now=1)
    assert sm.state == PositionState.LONG, "one opposing bar must not flip yet"
    assert any(l.reason_code == "MONITOR_HOLDING" for l in logs1)

    logs2 = sm.on_bar(_flat_df(price=1999.8, bullish_body=False), spread_points=1.0, now=2)
    events = [l.event for l in logs2]
    assert any(e.startswith("EXIT_LONG") for e in events)
    assert any(e.startswith("FLIP_LONG_TO_SHORT") for e in events)
    assert sm.state == PositionState.SHORT and sm.position is not None
    assert sm.flip_count == 1 and sm.direction_switch_count == 1

    flip_log = next(l for l in logs2 if l.event.startswith("FLIP_"))
    for field in ("old_direction", "old_entry", "close_price", "realized_pnl",
                  "reversal_reason", "new_direction", "new_entry"):
        assert field in flip_log.detail, f"flip log missing required field {field}"
    assert flip_log.detail["old_direction"] == "LONG"
    assert flip_log.detail["new_direction"] == "SHORT"


def test_flip_frequency_ceiling_blocks_excess_flips():
    p = _flip_params()
    p.max_flips_per_hour = 1
    sm = ScalperStateMachine(p, symbol="XAUUSD")

    long_reading = DirectionReading(Direction.LONG_STRONG, 90.0, "", atr_ratio=1.0, momentum_norm=0.9)
    short_reading = DirectionReading(Direction.SHORT_STRONG, -90.0, "", atr_ratio=1.0, momentum_norm=-0.9)

    sm.direction.detect = lambda _df: long_reading
    sm.on_bar(_flat_df(price=2000.0), spread_points=1.0, now=0)

    sm.direction.detect = lambda _df: short_reading
    sm.on_bar(_flat_df(price=1999.8), spread_points=1.0, now=1)
    sm.on_bar(_flat_df(price=1999.8, bullish_body=False), spread_points=1.0, now=2)
    assert sm.state == PositionState.SHORT and sm.flip_count == 1

    # Immediately confirm a reversal back to LONG — should be blocked by the
    # one-flip-per-hour ceiling and land FLAT instead of flipping again.
    sm.direction.detect = lambda _df: long_reading
    sm.on_bar(_flat_df(price=2000.0), spread_points=1.0, now=3)
    logs = sm.on_bar(_flat_df(price=2000.0), spread_points=1.0, now=4)
    assert sm.flip_count == 1, "second flip within the same hour must be blocked"
    assert any(l.reason_code == "BLOCKED_MAX_FLIP_FREQUENCY" for l in logs)
    assert sm.state == PositionState.FLAT
