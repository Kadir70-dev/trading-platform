"""Standalone backtest runner.

Usage:
    python backtest_cli.py --symbol XAUUSD --timeframe M5 --bars 5000
Pulls bars from the broker backend (sim or live) and prints a performance
summary. Used by `trader backtest` in the Go CLI as well.
"""
from __future__ import annotations

import argparse
import json

from config import get_settings
from mt5.client import build_broker
from strategies.ema_rsi import StrategyParams
from backtesting.backtester import run_backtest


def main():
    s = get_settings()
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default=s.default_symbol)
    ap.add_argument("--timeframe", default=s.timeframe)
    ap.add_argument("--bars", type=int, default=3000)
    ap.add_argument("--equity", type=float, default=100.0)
    ap.add_argument("--lot", type=float, default=s.default_lot)
    args = ap.parse_args()

    broker = build_broker(s)
    df = broker.ohlc(args.symbol.upper(), args.timeframe.upper(), args.bars)
    params = StrategyParams(
        ema_fast=s.ema_fast, ema_slow=s.ema_slow, rsi_period=s.rsi_period,
        rsi_buy_level=s.rsi_buy_level, rsi_sell_level=s.rsi_sell_level,
        sl_atr_mult=s.sl_atr_mult, tp_rr=s.tp_rr,
    )
    res = run_backtest(df, args.symbol.upper(), args.timeframe.upper(), params,
                       start_equity=args.equity, lot=args.lot)
    print(json.dumps({"symbol": args.symbol.upper(), "timeframe": args.timeframe.upper(),
                      "bars": len(df), **res.summary}, indent=2))


if __name__ == "__main__":
    main()
