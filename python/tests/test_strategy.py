"""Strategy + signal engine tests using hand-built OHLC that forces conditions."""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import numpy as np
import pandas as pd

from strategies.ema_rsi import EmaRsiStrategy, StrategyParams
from strategies.signal_generator import SignalGenerator


def _uptrend(n=200, start=100.0, step=0.5):
    close = np.array([start + i * step for i in range(n)], dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.ones(n),
    })


def _downtrend(n=200, start=200.0, step=0.5):
    close = np.array([start - i * step for i in range(n)], dtype=float)
    return pd.DataFrame({
        "open": close, "high": close + 0.2, "low": close - 0.2,
        "close": close, "volume": np.ones(n),
    })


def test_uptrend_triggers_buy():
    strat = EmaRsiStrategy(StrategyParams())
    sig = strat.evaluate("EURUSD", "M1", _uptrend())
    assert sig.side == "BUY"
    assert sig.sl is not None and sig.tp is not None
    assert sig.tp > sig.price > sig.sl  # BUY: tp above, sl below entry


def test_downtrend_triggers_sell():
    strat = EmaRsiStrategy(StrategyParams())
    sig = strat.evaluate("EURUSD", "M1", _downtrend())
    assert sig.side == "SELL"
    assert sig.sl > sig.price > sig.tp  # SELL: sl above, tp below entry


def test_rr_is_at_least_target():
    p = StrategyParams(tp_rr=2.0)
    strat = EmaRsiStrategy(p)
    sig = strat.evaluate("EURUSD", "M1", _uptrend())
    rr = (sig.tp - sig.price) / (sig.price - sig.sl)
    assert rr >= 1.99


def test_low_confidence_downgraded_to_hold():
    # Flat-ish market -> weak/no trend -> HOLD
    n = 200
    rng = np.random.default_rng(3)
    close = 100 + rng.normal(0, 0.01, n)  # essentially flat
    df = pd.DataFrame({"open": close, "high": close + 0.01, "low": close - 0.01,
                       "close": close, "volume": np.ones(n)})
    sg = SignalGenerator(StrategyParams())
    sig = sg.generate("EURUSD", "M1", df)
    assert sig.side == "HOLD"


def test_insufficient_data_holds():
    sg = SignalGenerator(StrategyParams())
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2],
                       "close": [1, 2], "volume": [1, 1]})
    sig = sg.generate("EURUSD", "M1", df)
    assert sig.side == "HOLD"
