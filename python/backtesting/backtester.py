"""Event-driven backtester for the EMA/RSI strategy.

Bar-by-bar walk-forward: at each closed bar, evaluate the strategy on the data
visible so far, manage a single position per symbol with SL/TP, and record the
equity curve. Intentionally simple and honest — no look-ahead.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from strategies.ema_rsi import EmaRsiStrategy, StrategyParams
from analytics.metrics import performance_summary


@dataclass
class Trade:
    side: str
    entry_idx: int
    entry: float
    sl: float
    tp: float
    exit_idx: int | None = None
    exit: float | None = None
    pnl: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    equity_curve: list[float] = field(default_factory=list)
    summary: dict = field(default_factory=dict)


def run_backtest(df: pd.DataFrame, symbol: str, timeframe: str,
                 params: StrategyParams, start_equity: float = 100.0,
                 lot: float = 0.01, contract: float = 100000.0) -> BacktestResult:
    strat = EmaRsiStrategy(params)
    equity = start_equity
    curve = [equity]
    trades: list[Trade] = []
    open_trade: Trade | None = None
    warmup = max(params.ema_slow, params.rsi_period) + 5

    for i in range(warmup, len(df)):
        window = df.iloc[: i + 1]
        bar = df.iloc[i]

        # Manage open trade against this bar's high/low
        if open_trade is not None:
            hit_sl = bar["low"] <= open_trade.sl if open_trade.side == "BUY" else bar["high"] >= open_trade.sl
            hit_tp = bar["high"] >= open_trade.tp if open_trade.side == "BUY" else bar["low"] <= open_trade.tp
            exit_px = None
            if hit_sl:
                exit_px = open_trade.sl
            elif hit_tp:
                exit_px = open_trade.tp
            if exit_px is not None:
                diff = (exit_px - open_trade.entry) if open_trade.side == "BUY" else (open_trade.entry - exit_px)
                open_trade.exit_idx = i
                open_trade.exit = exit_px
                open_trade.pnl = diff * lot * contract
                equity += open_trade.pnl
                trades.append(open_trade)
                open_trade = None

        # Look for a new entry only when flat
        if open_trade is None:
            sig = strat.evaluate(symbol, timeframe, window)
            if sig.side in ("BUY", "SELL") and sig.confidence >= params.min_confidence \
                    and sig.sl and sig.tp:
                open_trade = Trade(side=sig.side, entry_idx=i, entry=sig.price,
                                   sl=sig.sl, tp=sig.tp)
        curve.append(equity)

    summary = performance_summary([t.pnl for t in trades], curve, start_equity)
    return BacktestResult(trades=trades, equity_curve=curve, summary=summary)
