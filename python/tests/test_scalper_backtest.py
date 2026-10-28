"""Backtest engine test on synthetic data (fast, no external data files needed)."""
import numpy as np
import pandas as pd
import pytest

from scalper.backtest import run_backtest
from scalper.config import ScalperParams


def _synthetic_bars(n=600):
    i = np.arange(n)
    close = 2000.0 + 3.0 * np.sin(2 * np.pi * i / 100) + 0.3 * np.sin(2 * np.pi * i / 8)
    close_ask = close + 0.05
    times = pd.date_range("2024-01-01", periods=n, freq="min", tz="UTC")
    return pd.DataFrame({
        "time": times, "open": close - 0.05, "high": close + 0.3, "low": close - 0.3,
        "close": close, "close_ask": close_ask, "spread_close": close_ask - close,
        "volume_bid": 1.0,
    })


def _fast_params():
    p = ScalperParams(_env_file=None)
    p.stop_distance_usd = 0.5
    p.lot_size = 0.01
    p.contract_size = 100.0
    p.cooldown_sec = 0
    p.max_spread_points = 100.0
    return p


def test_backtest_runs_and_reports_consistent_stats():
    report = run_backtest(_synthetic_bars(), _fast_params())
    assert report.num_trades >= 1
    wins = round(report.win_rate_pct / 100.0 * report.num_trades)
    assert 0 <= wins <= report.num_trades
    if report.profit_factor is not None:
        assert report.profit_factor >= 0
    assert report.max_drawdown >= 0
    assert report.spread_cost_total >= 0
    assert abs(report.gross_pnl - report.spread_cost_total - report.net_pnl) < 1e-6


def test_backtest_raises_on_insufficient_bars():
    with pytest.raises(ValueError):
        run_backtest(_synthetic_bars(n=10), _fast_params())


def test_backtest_flips_are_counted_and_stay_internally_consistent():
    p = _fast_params()
    p.min_reversal_confirm_bars = 2
    report = run_backtest(_synthetic_bars(n=2000), p)

    assert report.num_trades >= 1
    assert abs(report.gross_pnl - report.spread_cost_total - report.net_pnl) < 1e-6
    flips = [t for t in report.trades if t.reentry_action == "FLIP"]
    assert report.flip_count == len(flips)
    for t in report.trades:
        assert t.mfe >= 0.0 and t.mae >= 0.0
