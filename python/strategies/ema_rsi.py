"""EMA20 / EMA50 / RSI14 trend-following strategy.

BUY  : EMA_fast > EMA_slow  AND  RSI > buy_level  AND  price > EMA_fast
SELL : EMA_fast < EMA_slow  AND  RSI < sell_level AND  price < EMA_fast
else : HOLD

Produces a Signal with a 0-100 confidence derived from how strongly each of the
three conditions is satisfied, plus SL/TP derived from ATR.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

import pandas as pd

from indicators.indicators import enrich


@dataclass
class StrategyParams:
    ema_fast: int = 20
    ema_slow: int = 50
    rsi_period: int = 14
    rsi_buy_level: float = 50.0
    rsi_sell_level: float = 50.0
    atr_period: int = 14
    sl_atr_mult: float = 1.5
    tp_rr: float = 2.0
    min_confidence: float = 55.0


@dataclass
class Signal:
    symbol: str
    timeframe: str
    side: str                 # BUY | SELL | HOLD
    confidence: float         # 0..100
    price: float
    ema_fast: float
    ema_slow: float
    rsi: float
    atr: float
    sl: Optional[float]
    tp: Optional[float]
    reason: str

    def to_dict(self) -> dict:
        return asdict(self)


class EmaRsiStrategy:
    name = "ema_rsi"

    def __init__(self, params: StrategyParams):
        self.p = params

    def _confidence(self, side: str, row: pd.Series) -> float:
        """Heuristic 0-100 confidence.

        Combines EMA separation (trend strength), RSI distance from the neutral
        level, and price displacement from the fast EMA, each normalised by ATR.
        """
        atr = row["atr"] if row["atr"] and row["atr"] > 0 else 1e-9
        ema_gap = abs(row["ema_fast"] - row["ema_slow"]) / atr        # in ATRs
        price_gap = abs(row["close"] - row["ema_fast"]) / atr
        if side == "BUY":
            rsi_strength = (row["rsi"] - self.p.rsi_buy_level)
        else:
            rsi_strength = (self.p.rsi_sell_level - row["rsi"])
        rsi_norm = max(0.0, rsi_strength) / 50.0                       # 0..~1

        # Weighted, squashed into 0..100
        raw = 0.45 * min(ema_gap, 1.0) + 0.35 * rsi_norm + 0.20 * min(price_gap, 1.0)
        return round(min(100.0, max(0.0, raw * 100.0)), 2)

    def evaluate(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Signal:
        """Evaluate the latest *closed* bar. `df` must be chronological OHLC."""
        enriched = enrich(
            df, self.p.ema_fast, self.p.ema_slow, self.p.rsi_period, self.p.atr_period
        )
        # Use the last fully formed bar (-1). Caller is responsible for passing
        # only closed candles.
        row = enriched.iloc[-1]
        price = float(row["close"])
        ef, es, r, a = (
            float(row["ema_fast"]),
            float(row["ema_slow"]),
            float(row["rsi"]),
            float(row["atr"]) if pd.notna(row["atr"]) else 0.0,
        )

        side, reason = "HOLD", "no condition met"
        if ef > es and r > self.p.rsi_buy_level and price > ef:
            side = "BUY"
            reason = f"EMA{self.p.ema_fast}>EMA{self.p.ema_slow}, RSI {r:.1f}>{self.p.rsi_buy_level}, price>EMA_fast"
        elif ef < es and r < self.p.rsi_sell_level and price < ef:
            side = "SELL"
            reason = f"EMA{self.p.ema_fast}<EMA{self.p.ema_slow}, RSI {r:.1f}<{self.p.rsi_sell_level}, price<EMA_fast"

        confidence = self._confidence(side, row) if side != "HOLD" else 0.0
        sl = tp = None
        if side != "HOLD" and a > 0:
            stop_dist = self.p.sl_atr_mult * a
            tp_dist = self.p.tp_rr * stop_dist
            if side == "BUY":
                sl, tp = price - stop_dist, price + tp_dist
            else:
                sl, tp = price + stop_dist, price - tp_dist

        # Below-threshold confidence is downgraded to HOLD by the signal engine,
        # but we keep the computed side/confidence for transparency/logging.
        return Signal(
            symbol=symbol, timeframe=timeframe, side=side, confidence=confidence,
            price=price, ema_fast=ef, ema_slow=es, rsi=r, atr=a,
            sl=round(sl, 6) if sl else None, tp=round(tp, 6) if tp else None,
            reason=reason,
        )
