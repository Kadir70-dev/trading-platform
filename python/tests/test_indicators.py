"""Indicator correctness tests."""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

from indicators.indicators import ema, rsi, atr, enrich


def test_ema_constant_series():
    s = pd.Series([5.0] * 50)
    out = ema(s, 10)
    # EMA of a constant series is the constant
    assert abs(out.iloc[-1] - 5.0) < 1e-9


def test_ema_responds_to_trend():
    s = pd.Series(np.arange(1, 101, dtype=float))
    fast = ema(s, 5).iloc[-1]
    slow = ema(s, 20).iloc[-1]
    # In a rising series the faster EMA leads the slower
    assert fast > slow


def test_rsi_bounds():
    rng = np.random.default_rng(0)
    s = pd.Series(100 + rng.normal(0, 1, 500).cumsum())
    r = rsi(s, 14).dropna()
    assert r.min() >= 0.0 and r.max() <= 100.0


def test_rsi_all_gains_is_100():
    s = pd.Series(np.arange(1, 60, dtype=float))  # strictly increasing
    r = rsi(s, 14)
    assert r.iloc[-1] > 99.0


def test_rsi_all_losses_is_0():
    s = pd.Series(np.arange(60, 1, -1, dtype=float))  # strictly decreasing
    r = rsi(s, 14)
    assert r.iloc[-1] < 1.0


def test_atr_positive():
    n = 100
    rng = np.random.default_rng(1)
    close = pd.Series(100 + rng.normal(0, 1, n).cumsum())
    high = close + 0.5
    low = close - 0.5
    a = atr(high, low, close, 14).dropna()
    assert (a > 0).all()


def test_enrich_columns():
    n = 120
    df = pd.DataFrame({
        "open": np.linspace(1, 2, n), "high": np.linspace(1.1, 2.1, n),
        "low": np.linspace(0.9, 1.9, n), "close": np.linspace(1, 2, n),
        "volume": np.ones(n),
    })
    out = enrich(df, 20, 50, 14, 14)
    for col in ("ema_fast", "ema_slow", "rsi", "atr"):
        assert col in out.columns
