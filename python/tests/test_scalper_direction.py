"""Direction detector tests using hand-built OHLC that forces conditions."""
import numpy as np
import pandas as pd

from scalper.config import ScalperParams
from scalper.direction import DirectionDetector
from scalper.types import Direction


def _wiggle_trend(n=200, start=2000.0, step=0.3, amplitude=1.5, period=10):
    i = np.arange(n)
    close = start + i * step + amplitude * np.sin(2 * np.pi * i / period)
    return pd.DataFrame({
        "open": close - 0.05, "high": close + 0.3, "low": close - 0.3, "close": close,
    })


def test_uptrend_is_long():
    reading = DirectionDetector(ScalperParams(_env_file=None)).detect(_wiggle_trend(step=0.3))
    assert reading.direction in (Direction.LONG_WEAK, Direction.LONG_STRONG)


def test_downtrend_is_short():
    reading = DirectionDetector(ScalperParams(_env_file=None)).detect(_wiggle_trend(step=-0.3))
    assert reading.direction in (Direction.SHORT_WEAK, Direction.SHORT_STRONG)


def test_flat_noisy_market_never_reads_strong():
    # Pure noise can occasionally drift into a weak directional read by chance,
    # but must never be mistaken for a STRONG, actionable signal.
    rng = np.random.default_rng(7)
    n = 200
    close = 2000 + rng.normal(0, 0.05, n)
    df = pd.DataFrame({"open": close, "high": close + 0.02, "low": close - 0.02, "close": close})
    reading = DirectionDetector(ScalperParams(_env_file=None)).detect(df)
    assert reading.direction not in (Direction.LONG_STRONG, Direction.SHORT_STRONG)


def test_insufficient_data_is_neutral():
    df = pd.DataFrame({"open": [1, 2], "high": [1, 2], "low": [1, 2], "close": [1, 2]})
    reading = DirectionDetector(ScalperParams(_env_file=None)).detect(df)
    assert reading.direction == Direction.NEUTRAL
    assert "insufficient" in reading.reason
