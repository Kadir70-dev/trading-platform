"""Performance metrics for backtests and live review."""
from __future__ import annotations

import math
from typing import Sequence


def performance_summary(pnls: Sequence[float], equity_curve: Sequence[float],
                        start_equity: float) -> dict:
    n = len(pnls)
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    net = sum(pnls)

    win_rate = (len(wins) / n * 100.0) if n else 0.0
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    expectancy = (net / n) if n else 0.0

    # Max drawdown on the equity curve
    peak = -math.inf
    max_dd = 0.0
    for v in equity_curve:
        peak = max(peak, v)
        if peak > 0:
            max_dd = max(max_dd, (peak - v) / peak * 100.0)

    # Simple per-trade Sharpe proxy
    if n > 1:
        mean = net / n
        var = sum((p - mean) ** 2 for p in pnls) / (n - 1)
        std = math.sqrt(var)
        sharpe = (mean / std) * math.sqrt(n) if std > 0 else 0.0
    else:
        sharpe = 0.0

    return {
        "trades": n,
        "wins": len(wins),
        "losses": len(losses),
        "win_rate_pct": round(win_rate, 2),
        "net_pnl": round(net, 2),
        "gross_win": round(gross_win, 2),
        "gross_loss": round(gross_loss, 2),
        "profit_factor": round(profit_factor, 3) if profit_factor != float("inf") else None,
        "expectancy": round(expectancy, 4),
        "max_drawdown_pct": round(max_dd, 2),
        "sharpe_proxy": round(sharpe, 3),
        "return_pct": round(net / start_equity * 100.0, 2) if start_equity else 0.0,
        "final_equity": round(start_equity + net, 2),
    }
