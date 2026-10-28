"""Offline bar-replay backtester for the scalper state machine.

Loads historical XAUUSD M1 bid/ask bars, replays every bar through the same
entry/exit/reentry/risk-guard logic the live engine uses, and reports the
statistics needed for historical validation before any demo broker trading is
enabled.

Deliberately not a per-bar pandas/DataFrame loop (too slow for millions of M1
bars): direction.compute_series scores every bar's structure/momentum/
volatility once, vectorized; only the inherently sequential position-
management loop runs bar-by-bar, and that loop does O(1) scalar work per bar,
reusing the exact same EntryConfirmer/ExitEvaluator/RiskGuard/ReentryGate
classes the live engine uses so behaviour cannot silently diverge.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np
import pandas as pd

from .config import ScalperParams
from .direction import compute_series
from .entry import EntryConfirmer
from .exit import ExitEvaluator
from .hedge import leg_pnl
from .reentry import ReentryGate
from .risk_guards import RiskGuard
from .types import Direction, DirectionReading, Position, PositionState


def load_dukascopy_m1(raw_dir: str, first_year: int, last_year: int) -> pd.DataFrame:
    """Merge monthly Dukascopy M1 bid+ask JSON dumps (see
    scripts/download_dukascopy_m1.cjs) into one chronological bar DataFrame:
    open/high/low/close from the bid side, close_ask + spread_close for
    spread-cost modelling. Drops zero-volume (non-trading) bars and
    de-duplicates by timestamp.
    """
    frames = []
    for year in range(first_year, last_year + 1):
        for month in range(1, 13):
            label = f"{year}-{month:02d}"
            bid_path = os.path.join(raw_dir, f"{label}-bid.json")
            ask_path = os.path.join(raw_dir, f"{label}-ask.json")
            if not (os.path.exists(bid_path) and os.path.exists(ask_path)):
                continue
            with open(bid_path) as f:
                bid = pd.DataFrame(json.load(f))
            with open(ask_path) as f:
                ask = pd.DataFrame(json.load(f))
            bid = bid.rename(columns={c: f"{c}_bid" for c in ("open", "high", "low", "close", "volume")})
            ask = ask.rename(columns={c: f"{c}_ask" for c in ("open", "high", "low", "close", "volume")})
            frames.append(pd.merge(bid, ask, on="timestamp", how="inner"))
    if not frames:
        raise RuntimeError(f"no Dukascopy M1 files found in {raw_dir} for {first_year}-{last_year}")

    df = pd.concat(frames, ignore_index=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    df = df[(df["volume_bid"] > 0) & (df["volume_ask"] > 0)].reset_index(drop=True)
    df["time"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)

    out = df.rename(columns={"open_bid": "open", "high_bid": "high",
                              "low_bid": "low", "close_bid": "close"})
    out["spread_close"] = out["close_ask"] - out["close"]
    return out[["time", "open", "high", "low", "close", "close_ask", "spread_close", "volume_bid"]]


@dataclass
class Trade:
    direction: str
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    pnl: float
    spread_cost: float
    bars_held: int
    exit_reason: str
    reentry_action: str
    mfe: float = 0.0     # max favorable excursion, price-distance
    mae: float = 0.0     # max adverse excursion, price-distance (positive number)

    def to_dict(self) -> dict:
        d = dict(self.__dict__)
        d["entry_time"], d["exit_time"] = str(d["entry_time"]), str(d["exit_time"])
        return d


@dataclass
class BacktestReport:
    num_trades: int
    avg_holding_bars: float
    win_rate_pct: float
    avg_win: float
    avg_loss: float
    profit_factor: Optional[float]
    max_drawdown: float
    max_consecutive_losses: int
    direction_switches: int
    same_direction_reentries: int
    gross_pnl: float
    net_pnl: float
    spread_cost_total: float
    spread_impact_pct: Optional[float]
    flip_count: int = 0
    trades: List[Trade] = field(default_factory=list)

    def summary_dict(self, include_trades: bool = True) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k != "trades"}
        if include_trades:
            d["trades"] = [t.to_dict() for t in self.trades]
        return d


def _empty_report(direction_switches: int, same_direction_reentries: int,
                   flip_count: int = 0) -> BacktestReport:
    return BacktestReport(0, 0.0, 0.0, 0.0, 0.0, None, 0.0, 0,
                           direction_switches, same_direction_reentries, 0.0, 0.0, 0.0, None,
                           flip_count, [])


def run_backtest(bars: pd.DataFrame, params: ScalperParams) -> BacktestReport:
    n = len(bars)
    need = max(params.structure_lookback, params.momentum_period, params.atr_period) + 5
    if n <= need:
        raise ValueError(f"not enough bars ({n}) for warmup ({need})")

    series = compute_series(bars, params)
    close = bars["close"].to_numpy()
    open_ = bars["open"].to_numpy()
    spread_points = bars["spread_close"].to_numpy() / params.point_size
    times = bars["time"]  # kept as a Series so .iat[i] yields a real pd.Timestamp (needs .date())
    direction_arr = series["direction"].to_numpy()
    atr_ratio_arr = series["atr_ratio"].to_numpy()
    momentum_arr = series["momentum_norm"].to_numpy()
    structure_arr = series["structure_score"].to_numpy()
    score_arr = series["score"].to_numpy()

    entry = EntryConfirmer(params)
    exiter = ExitEvaluator(params)
    # auto_reset_daily=True: a multi-year backtest with a single continuous
    # RiskGuard would otherwise measure "what happens after the kill-switch
    # trips once and is never touched again" rather than the strategy's
    # actual edge. The live runner must never do this (see risk_guards.py).
    guard = RiskGuard(params, auto_reset_daily=True)
    reentry = ReentryGate()

    state = PositionState.FLAT
    position: Optional[Position] = None
    current_reentry_action = "ENTRY_INITIAL"
    trades: List[Trade] = []
    direction_switches = 0
    same_direction_reentries = 0
    flip_count = 0
    max_streak = 0
    risk_unit_const = params.stop_distance_usd  # price distance, independent of lot_size

    def close_trade(pos: Position, price: float, exit_i: int, now, reason_code: str,
                     reentry_action: str) -> Trade:
        gross = leg_pnl(pos, price, params)
        spread_cost = ((float(spread_points[pos.entry_bar_index]) + float(spread_points[exit_i]))
                       * params.point_size * params.lot_size * pos.lot_multiplier * params.contract_size)
        net = gross - spread_cost
        return Trade(pos.direction.value, pos.entry_time, now, pos.entry_price, price, net, spread_cost,
                     exit_i - pos.entry_bar_index, reason_code, reentry_action,
                     mfe=pos.peak_favorable_move, mae=-pos.worst_move)

    def open_position(direction: PositionState, price: float, now, i: int) -> Position:
        return Position(direction, price, now, i, risk_unit_const)

    for i in range(need, n):
        now = times.iat[i]
        reading = DirectionReading(Direction(direction_arr[i]), float(score_arr[i]), "",
                                    float(atr_ratio_arr[i]), float(momentum_arr[i]), float(structure_arr[i]))
        price = float(close[i])

        if state == PositionState.FLAT:
            gr = guard.check_pretrade(float(spread_points[i]), now)
            if not gr.ok or reading.direction not in (Direction.LONG_STRONG, Direction.SHORT_STRONG):
                continue
            ed = entry.confirm(reading, float(close[i] - open_[i]), float(spread_points[i]))
            if not ed.should_enter:
                continue

            new_state = (PositionState.LONG if reading.direction == Direction.LONG_STRONG
                         else PositionState.SHORT)
            position = open_position(new_state, price, now, i)
            state = new_state

            rd = reentry.classify_entry(new_state)
            current_reentry_action = rd.action
            if rd.action == "REENTRY_SAME_DIRECTION":
                same_direction_reentries += 1
            elif rd.action == "REENTRY_DIRECTION_SWITCH":
                direction_switches += 1
            continue

        assert position is not None
        xd = exiter.evaluate(position, price, reading, reading.momentum_norm, i)
        if not xd.should_exit:
            continue

        if xd.reason_code == "EXIT_REVERSAL_CONFIRMED_FLIP":
            old_direction = position.direction
            trade = close_trade(position, price, i, now, xd.reason_code, current_reentry_action)
            guard.register_trade_result(trade.pnl, now)
            max_streak = max(max_streak, guard.consecutive_losses)
            reentry.record_exit(old_direction, now)
            trades.append(trade)
            state, position = PositionState.FLAT, None

            # Immediate close-and-flip: bypasses the ordinary cooldown (a
            # confirmed reversal is a deliberate signal, not overtrading),
            # gated only by the anti-churn flip-frequency ceiling and a fresh
            # spread/entry-quality re-check — never by the wide old stop.
            fg = guard.check_flip_allowed(now)
            new_direction = PositionState.SHORT if old_direction == PositionState.LONG else PositionState.LONG
            expected = Direction.SHORT_STRONG if new_direction == PositionState.SHORT else Direction.LONG_STRONG
            if fg.ok and reading.direction == expected:
                ed = entry.confirm(reading, float(close[i] - open_[i]), float(spread_points[i]))
                if ed.should_enter:
                    position = open_position(new_direction, price, now, i)
                    state = new_direction
                    guard.register_flip(now)
                    direction_switches += 1
                    flip_count += 1
                    current_reentry_action = "FLIP"
            continue

        trade = close_trade(position, price, i, now, xd.reason_code, current_reentry_action)
        guard.register_trade_result(trade.pnl, now)
        max_streak = max(max_streak, guard.consecutive_losses)
        reentry.record_exit(position.direction, now)
        trades.append(trade)
        state, position = PositionState.FLAT, None

    if not trades:
        return _empty_report(direction_switches, same_direction_reentries, flip_count)
    return _summarize(trades, direction_switches, same_direction_reentries, max_streak, flip_count)


def _summarize(trades: List[Trade], direction_switches: int, same_direction_reentries: int,
               max_streak: int, flip_count: int = 0) -> BacktestReport:
    pnls = np.array([t.pnl for t in trades])
    wins = pnls[pnls > 0]
    losses = pnls[pnls < 0]
    spread_cost_total = float(sum(t.spread_cost for t in trades))
    net_pnl = float(pnls.sum())
    gross_pnl = net_pnl + spread_cost_total

    equity = np.cumsum(pnls)
    peak = np.maximum.accumulate(equity)
    max_dd = float((peak - equity).max())

    profit_factor = float(wins.sum() / abs(losses.sum())) if len(losses) and losses.sum() != 0 else None
    spread_impact_pct = (spread_cost_total / abs(gross_pnl) * 100.0) if gross_pnl else None

    return BacktestReport(
        num_trades=len(trades),
        avg_holding_bars=float(np.mean([t.bars_held for t in trades])),
        win_rate_pct=float(len(wins) / len(trades) * 100.0),
        avg_win=float(wins.mean()) if len(wins) else 0.0,
        avg_loss=float(losses.mean()) if len(losses) else 0.0,
        profit_factor=profit_factor,
        max_drawdown=max_dd,
        max_consecutive_losses=max_streak,
        direction_switches=direction_switches,
        same_direction_reentries=same_direction_reentries,
        gross_pnl=gross_pnl,
        net_pnl=net_pnl,
        spread_cost_total=spread_cost_total,
        spread_impact_pct=spread_impact_pct,
        flip_count=flip_count,
        trades=trades,
    )
