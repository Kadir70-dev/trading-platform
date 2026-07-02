"""Signal engine: routes a symbol to the right strategy, applies the confidence
floor, and emits a normalised BUY / SELL / HOLD signal.
"""
from __future__ import annotations

import pandas as pd
from loguru import logger

from strategies.ema_rsi import EmaRsiStrategy, StrategyParams, Signal
from strategies.gold_strategy import GoldStrategy


class SignalGenerator:
    def __init__(self, params: StrategyParams):
        self.params = params
        self._default = EmaRsiStrategy(params)
        self._gold = GoldStrategy(params)

    def _strategy_for(self, symbol: str) -> EmaRsiStrategy:
        if symbol.upper().startswith("XAU"):
            return self._gold
        return self._default

    def generate(self, symbol: str, timeframe: str, df: pd.DataFrame) -> Signal:
        if df is None or len(df) < max(self.params.ema_slow, self.params.rsi_period) + 5:
            logger.warning("Not enough bars for {} ({} rows)", symbol, 0 if df is None else len(df))
            return Signal(symbol, timeframe, "HOLD", 0.0, 0, 0, 0, 0, 0, None, None,
                          "insufficient data")

        strat = self._strategy_for(symbol)
        sig = strat.evaluate(symbol, timeframe, df)

        # Apply confidence floor: weak signals become HOLD.
        if sig.side != "HOLD" and sig.confidence < strat.p.min_confidence:
            logger.info("{} {} downgraded to HOLD (conf {:.1f} < {:.1f})",
                        symbol, sig.side, sig.confidence, strat.p.min_confidence)
            sig.reason = f"{sig.side} below confidence floor ({sig.confidence:.1f}<{strat.p.min_confidence:.1f})"
            sig.side = "HOLD"
            sig.sl = sig.tp = None

        logger.info("SIGNAL {} {} {} conf={:.1f} px={:.5f} rsi={:.1f} ema={:.5f}/{:.5f}",
                    symbol, timeframe, sig.side, sig.confidence, sig.price,
                    sig.rsi, sig.ema_fast, sig.ema_slow)
        return sig
