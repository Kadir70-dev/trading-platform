"""XAUUSD (Gold) specialisation of the EMA/RSI strategy.

Gold is more volatile and spreads are wider, so we widen stops and demand a
slightly higher confidence before acting. Same signal logic, tuned parameters.
"""
from __future__ import annotations

import pandas as pd

from strategies.ema_rsi import EmaRsiStrategy, StrategyParams, Signal


class GoldStrategy(EmaRsiStrategy):
    name = "gold_strategy"

    def __init__(self, base: StrategyParams | None = None):
        p = base or StrategyParams()
        gold = StrategyParams(
            ema_fast=p.ema_fast,
            ema_slow=p.ema_slow,
            rsi_period=p.rsi_period,
            rsi_buy_level=p.rsi_buy_level,
            rsi_sell_level=p.rsi_sell_level,
            atr_period=p.atr_period,
            sl_atr_mult=max(2.0, p.sl_atr_mult),   # wider stop for gold
            tp_rr=max(2.0, p.tp_rr),
            min_confidence=max(60.0, p.min_confidence),
        )
        super().__init__(gold)

    def evaluate(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Signal:
        return super().evaluate(symbol, timeframe, df)
