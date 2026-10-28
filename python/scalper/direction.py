"""Direction detection: a transparent, rule-based read of short-term price
structure, momentum, and volatility regime. No ML.

Produces one of five graded readings (LONG_STRONG..SHORT_STRONG) that the
entry/exit/reentry modules act on. Only LONG_STRONG/SHORT_STRONG are
actionable for entries (see entry.py); the WEAK/NEUTRAL grades exist so exit
logic can detect a fading trend before it fully reverses.

`compute_series` is the single source of truth for the math, fully vectorized
so the backtester can score millions of bars without a per-bar Python loop.
`detect()` (the live, single-latest-bar call) is a thin wrapper around it, so
the live engine and the backtest can never numerically diverge.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from indicators.indicators import atr as atr_series

from .config import ScalperParams
from .types import Direction, DirectionReading


def compute_series(df: pd.DataFrame, params: ScalperParams) -> pd.DataFrame:
    """Vectorized per-bar structure/momentum/volatility/direction scoring.

    Structure: confirmed 5-bar fractal swing highs/lows within the trailing
    `structure_lookback` bars — higher-highs+higher-lows is bullish,
    lower-highs+lower-lows is bearish, anything else is neutral/ambiguous.

    Momentum: `momentum_period`-bar price change normalised by 1.5x ATR,
    clipped to [-1, 1].

    Volatility regime: current ATR vs its own rolling median. Below
    `chop_atr_floor_pct` of that median forces NEUTRAL regardless of
    structure/momentum — the "avoid noisy/choppy conditions" filter.
    """
    n = len(df)
    lookback = params.structure_lookback
    high = df["high"].to_numpy(dtype=float)
    low = df["low"].to_numpy(dtype=float)
    close = df["close"].to_numpy(dtype=float)

    roll_max5 = pd.Series(high).rolling(5, center=True).max().to_numpy()
    roll_min5 = pd.Series(low).rolling(5, center=True).min().to_numpy()
    is_swing_high = high == roll_max5   # NaN edges compare False -> excluded
    is_swing_low = low == roll_min5

    swing_high_idx = np.flatnonzero(is_swing_high)
    swing_high_val = high[swing_high_idx]
    swing_low_idx = np.flatnonzero(is_swing_low)
    swing_low_val = low[swing_low_idx]

    idx_all = np.arange(n)
    pos_h = np.searchsorted(swing_high_idx, idx_all, side="right") - 1
    pos_l = np.searchsorted(swing_low_idx, idx_all, side="right") - 1

    def _last_two(idx_arr: np.ndarray, val_arr: np.ndarray, pos: np.ndarray):
        if len(idx_arr) < 2:
            # Fewer than 2 swing points exist anywhere yet — nothing to compare.
            no_val = np.full(len(pos), np.nan)
            return no_val, no_val, np.zeros(len(pos), dtype=bool)
        has_two = pos >= 1
        safe_pos = np.clip(pos, 1, len(idx_arr) - 1)
        last_idx, last_val = idx_arr[safe_pos], val_arr[safe_pos]
        prev_idx, prev_val = idx_arr[safe_pos - 1], val_arr[safe_pos - 1]
        within_window = has_two & (last_idx >= idx_all - lookback) & (prev_idx >= idx_all - lookback)
        return last_val, prev_val, within_window

    last_h, prev_h, ok_h = _last_two(swing_high_idx, swing_high_val, pos_h)
    last_l, prev_l, ok_l = _last_two(swing_low_idx, swing_low_val, pos_l)

    have_structure = ok_h & ok_l
    bullish = have_structure & (last_h > prev_h) & (last_l > prev_l)
    bearish = have_structure & (last_h < prev_h) & (last_l < prev_l)
    structure_score = np.where(bullish, 1.0, np.where(bearish, -1.0, 0.0))

    atr_all = atr_series(df["high"], df["low"], df["close"], params.atr_period).to_numpy()
    atr_ref = pd.Series(atr_all).rolling(lookback * 3, min_periods=1).median().to_numpy()
    atr_ratio = np.where(atr_ref > 0, atr_all / atr_ref, 1.0)

    period = params.momentum_period
    raw_move = close - np.roll(close, period)
    raw_move[:period] = 0.0
    atr_for_norm = np.where(atr_all > 0, atr_all, 1e-9)
    momentum_norm = np.clip(raw_move / (atr_for_norm * 1.5), -1.0, 1.0)

    is_choppy = atr_ratio < (params.chop_atr_floor_pct / 100.0)
    combined = 100.0 * (0.5 * structure_score + 0.5 * momentum_norm)
    combined = np.where(is_choppy, 0.0, combined)

    strong, weak = params.direction_strong_threshold, params.direction_weak_threshold
    direction = np.full(n, Direction.NEUTRAL.value, dtype=object)
    direction[(combined >= weak) & (combined < strong)] = Direction.LONG_WEAK.value
    direction[combined >= strong] = Direction.LONG_STRONG.value
    direction[(combined <= -weak) & (combined > -strong)] = Direction.SHORT_WEAK.value
    direction[combined <= -strong] = Direction.SHORT_STRONG.value
    direction[is_choppy] = Direction.NEUTRAL.value

    need = max(lookback, period, params.atr_period) + 5
    direction[:min(need, n)] = Direction.NEUTRAL.value

    return pd.DataFrame({
        "structure_score": structure_score, "momentum_norm": momentum_norm,
        "atr_ratio": atr_ratio, "score": combined, "direction": direction,
    })


def _structure_desc(score: float) -> str:
    if score > 0:
        return "higher-highs/higher-lows (bullish structure)"
    if score < 0:
        return "lower-highs/lower-lows (bearish structure)"
    return "mixed/ambiguous or insufficient swing points"


class DirectionDetector:
    def __init__(self, params: ScalperParams):
        self.p = params

    def detect(self, df: pd.DataFrame) -> DirectionReading:
        need = max(self.p.structure_lookback, self.p.momentum_period, self.p.atr_period) + 5
        if df is None or len(df) < need:
            return DirectionReading(Direction.NEUTRAL, 0.0, "insufficient data")

        row = compute_series(df, self.p).iloc[-1]
        direction = Direction(row["direction"])
        atr_ratio, momentum_norm = float(row["atr_ratio"]), float(row["momentum_norm"])
        structure_score, combined = float(row["structure_score"]), float(row["score"])

        chop_floor = self.p.chop_atr_floor_pct / 100.0
        if atr_ratio < chop_floor:
            reason = (f"NEUTRAL (choppy market): atr_ratio={atr_ratio:.2f} below floor "
                      f"{chop_floor:.2f}; structure={_structure_desc(structure_score)}, "
                      f"momentum_norm={momentum_norm:.2f}")
        else:
            reason = (f"score={combined:.1f}: structure={_structure_desc(structure_score)}, "
                      f"momentum_norm={momentum_norm:.2f}, atr_ratio={atr_ratio:.2f}")
        return DirectionReading(direction, combined, reason, atr_ratio, momentum_norm, structure_score)
