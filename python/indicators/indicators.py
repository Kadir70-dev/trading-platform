"""Technical indicators.

These are the source of truth for the platform. We implement EMA / RSI / ATR
directly (Wilder's smoothing for RSI/ATR) so behaviour is deterministic and does
not silently change with a third-party library upgrade. The `ta` package is an
optional cross-check only.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    """Exponential moving average (standard, adjust=False)."""
    if period <= 0:
        raise ValueError("period must be positive")
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    """Relative Strength Index using Wilder's smoothing (RMA)."""
    if period <= 0:
        raise ValueError("period must be positive")
    delta = series.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    # Wilder's smoothing == EMA with alpha = 1/period
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0.0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    # When avg_loss == 0 → RSI 100; when avg_gain == 0 → RSI 0
    out = out.where(avg_loss != 0.0, 100.0)
    out = out.where(avg_gain != 0.0, out.where(avg_loss == 0.0, 0.0))
    return out


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Average True Range using Wilder's smoothing."""
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / period, adjust=False, min_periods=period).mean()


def enrich(df: pd.DataFrame, ema_fast: int, ema_slow: int,
           rsi_period: int, atr_period: int = 14) -> pd.DataFrame:
    """Attach indicator columns to an OHLC DataFrame.

    Expects columns: open, high, low, close, (volume optional).
    Returns a copy with ema_fast / ema_slow / rsi / atr columns added.
    """
    out = df.copy()
    out["ema_fast"] = ema(out["close"], ema_fast)
    out["ema_slow"] = ema(out["close"], ema_slow)
    out["rsi"] = rsi(out["close"], rsi_period)
    out["atr"] = atr(out["high"], out["low"], out["close"], atr_period)
    return out
