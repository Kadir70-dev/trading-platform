"""STRATEGY PRESERVATION golden regression.

Locks the EXACT BUY/SELL/EXIT decision stream the XAUUSD discretionary scalper
produces on a fixed, deterministic market series. The golden below was captured
from the pre-Step-2 baseline commit c913a2e and verified byte-identical on the
post-Step-2 commit 7e2db8f — proving the reconciliation fixes did not alter any
strategy decision.

If this test fails, a change touched direction/momentum scoring, the combined
threshold, entry/candle confirmation, trailing activation, the 40% giveback,
reversal/flip, re-entry or the emergency stop — i.e. the locked strategy. That
is only acceptable with an explicit, approved strategy change (not this phase).

Decisions only (event + reason_code + state); P&L is deliberately excluded
because internal vs broker P&L handling is expected to differ by design.
"""
import hashlib
import json

import numpy as np
import pandas as pd

from scalper.config import ScalperParams
from scalper.state_machine import ScalperStateMachine

# ---- deterministic market data (the project's own characterization pattern) --
def _wiggle_trend(n, start, step, amplitude=1.5, period=10) -> pd.DataFrame:
    i = np.arange(n)
    close = start + i * step + amplitude * np.sin(2 * np.pi * i / period)
    return pd.DataFrame({
        "open": close - 0.05, "high": close + 0.3, "low": close - 0.3, "close": close,
    })


def _bars() -> pd.DataFrame:
    up = _wiggle_trend(150, 2000.0, step=0.4)
    down = _wiggle_trend(150, float(up["close"].iloc[-1]), step=-0.4)
    df = pd.concat([up, down], ignore_index=True)
    df["volume"] = 100
    df["time"] = pd.to_datetime([1_700_000_000 + 60 * j for j in range(len(df))], unit="s")
    return df


def _params() -> ScalperParams:
    p = ScalperParams(_env_file=None)
    p.stop_distance_usd = 1.0
    p.lot_size = 0.01
    p.contract_size = 100.0
    p.cooldown_sec = 0
    p.max_spread_points = 100.0
    return p


def _decision_stream() -> list:
    sm = ScalperStateMachine(_params(), symbol="XAUUSD")   # default pnl_mode (internal)
    bars = _bars()
    stream = []
    for i in range(30, len(bars)):
        for d in sm.on_bar(bars.iloc[: i + 1], spread_points=1.0, now=i):
            stream.append([i, d.event, d.reason_code, d.state])
    return stream


# ---- GOLDEN (captured at c913a2e, confirmed identical at 7e2db8f) ------------
GOLDEN_TOTAL = 270
GOLDEN_SHA256 = "10c5997a31004f47f2502586baf138afb5eecb4cd8f486752370fb0e273c00a1"
GOLDEN_SIGNALS = [
    [30, "ENTER_LONG", "ENTRY_CONFIRMED"], [36, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [40, "ENTER_LONG", "ENTRY_CONFIRMED"], [46, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [50, "ENTER_LONG", "ENTRY_CONFIRMED"], [56, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [60, "ENTER_LONG", "ENTRY_CONFIRMED"], [66, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [70, "ENTER_LONG", "ENTRY_CONFIRMED"], [76, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [80, "ENTER_LONG", "ENTRY_CONFIRMED"], [86, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [90, "ENTER_LONG", "ENTRY_CONFIRMED"], [96, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [100, "ENTER_LONG", "ENTRY_CONFIRMED"], [106, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [110, "ENTER_LONG", "ENTRY_CONFIRMED"], [116, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [120, "ENTER_LONG", "ENTRY_CONFIRMED"], [126, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [130, "ENTER_LONG", "ENTRY_CONFIRMED"], [136, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [140, "ENTER_LONG", "ENTRY_CONFIRMED"], [146, "EXIT_LONG", "EXIT_PROFIT_TRAIL_STOP"],
    [150, "ENTER_LONG", "ENTRY_CONFIRMED"], [156, "EXIT_LONG", "EXIT_EMERGENCY_STOP"],
]


def test_decision_count_locked():
    assert len(_decision_stream()) == GOLDEN_TOTAL


def test_signal_events_locked():
    stream = _decision_stream()
    signals = [[x[0], x[1], x[2]] for x in stream if not x[1].startswith(("HOLD", "MONITOR"))]
    assert signals == GOLDEN_SIGNALS


def test_full_decision_digest_locked():
    stream = _decision_stream()
    canon = json.dumps(stream, separators=(",", ":"), sort_keys=True)
    assert hashlib.sha256(canon.encode()).hexdigest() == GOLDEN_SHA256


def test_strategy_knobs_unchanged():
    """The locked strategy constants must not drift (defense in depth)."""
    p = ScalperParams(_env_file=None)
    assert p.direction_strong_threshold == 60.0
    assert p.direction_weak_threshold == 20.0
    assert p.momentum_period == 5 and p.structure_lookback == 20 and p.atr_period == 14
    assert p.trail_activate_r == 1.0 and p.trail_giveback_pct == 0.4
    assert p.min_reversal_confirm_bars == 2 and p.emergency_stop_r_mult == 3.0
    assert p.chop_atr_floor_pct == 40.0
