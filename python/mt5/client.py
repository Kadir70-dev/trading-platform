"""Broker abstraction with two backends:

* MT5Broker  — real Exness MT5 via the MetaTrader5 package (Wine on Linux).
* SimBroker  — deterministic in-memory simulator used when the terminal is not
               reachable, so the full pipeline (data → signal → risk → exec) can
               run and be tested on plain Ubuntu.

Both expose the same interface, consumed by service.py.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd
from loguru import logger

from mt5.connection import MT5Connection, MT5_AVAILABLE

try:
    import MetaTrader5 as mt5  # type: ignore
except Exception:  # pragma: no cover
    mt5 = None  # type: ignore

# Logical timeframe -> (mt5 constant name, seconds per bar)
TF_SECONDS = {"M1": 60, "M5": 300, "M15": 900, "M30": 1800, "H1": 3600}


@dataclass
class OrderResult:
    ok: bool
    retcode: int
    retcode_desc: str
    ticket: int | None
    price: float | None
    volume: float | None
    spread_points: float | None
    raw: dict


class Broker:
    backend = "base"

    def connect(self) -> bool: ...
    def shutdown(self) -> None: ...
    def account_info(self) -> dict: ...
    def positions(self) -> list[dict]: ...
    def history(self, days: int = 7) -> list[dict]: ...
    def symbol_info(self, symbol: str) -> dict: ...
    def tick(self, symbol: str) -> dict: ...
    def ohlc(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame: ...
    def market_order(self, symbol: str, side: str, volume: float,
                     sl: float | None, tp: float | None,
                     magic: int, comment: str) -> OrderResult: ...
    def close_position(self, ticket: int, volume: float | None = None) -> OrderResult: ...
    def modify(self, ticket: int, sl: float | None, tp: float | None) -> OrderResult: ...


# ----------------------------------------------------------------------------
# Real MT5 backend
# ----------------------------------------------------------------------------
class MT5Broker(Broker):
    backend = "mt5"

    def __init__(self, conn: MT5Connection):
        self.conn = conn

    def connect(self) -> bool:
        return self.conn.connect()

    def shutdown(self) -> None:
        self.conn.shutdown()

    def _tf_const(self, timeframe: str):
        return getattr(mt5, f"TIMEFRAME_{timeframe}")

    def account_info(self) -> dict:
        info = mt5.account_info()
        ident = self.conn.identity()
        return {
            "login": info.login,
            "broker": getattr(info, "company", "UNKNOWN"),
            "server": info.server,
            "mode": ident.mode if ident else "UNKNOWN",
            "currency": info.currency,
            "leverage": info.leverage,
            "balance": info.balance,
            "equity": info.equity,
            "margin": info.margin,
            "free_margin": info.margin_free,
            "margin_level": info.margin_level,
        }

    def positions(self) -> list[dict]:
        ps = mt5.positions_get() or []
        out = []
        for p in ps:
            out.append({
                "ticket": p.ticket, "symbol": p.symbol,
                "side": "BUY" if p.type == mt5.POSITION_TYPE_BUY else "SELL",
                "volume": p.volume, "entry_price": p.price_open,
                "sl": p.sl, "tp": p.tp, "swap": p.swap, "profit": p.profit,
            })
        return out

    def history(self, days: int = 7) -> list[dict]:
        to = int(time.time())
        frm = to - days * 86400
        deals = mt5.history_deals_get(frm, to) or []
        return [{
            "ticket": d.ticket, "order": d.order, "symbol": d.symbol,
            "type": d.type, "volume": d.volume, "price": d.price,
            "profit": d.profit, "commission": d.commission, "swap": d.swap,
            "time": d.time,
        } for d in deals]

    def symbol_info(self, symbol: str) -> dict:
        mt5.symbol_select(symbol, True)
        s = mt5.symbol_info(symbol)
        t = mt5.symbol_info_tick(symbol)
        spread_points = (t.ask - t.bid) / s.point if s.point else 0.0
        return {
            "symbol": symbol, "digits": s.digits, "point": s.point,
            "bid": t.bid, "ask": t.ask, "spread_points": round(spread_points, 1),
            "volume_min": s.volume_min, "volume_max": s.volume_max,
            "volume_step": s.volume_step, "trade_allowed": s.trade_mode != 0,
            "tick_value": s.trade_tick_value, "tick_size": s.trade_tick_size,
            "contract_size": s.trade_contract_size,
        }

    def tick(self, symbol: str) -> dict:
        t = mt5.symbol_info_tick(symbol)
        return {"symbol": symbol, "bid": t.bid, "ask": t.ask, "time": t.time}

    def ohlc(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        mt5.symbol_select(symbol, True)
        rates = mt5.copy_rates_from_pos(symbol, self._tf_const(timeframe), 0, count)
        if rates is None or len(rates) == 0:
            return pd.DataFrame(columns=["time", "open", "high", "low", "close", "volume"])
        df = pd.DataFrame(rates)
        df.rename(columns={"tick_volume": "volume"}, inplace=True)
        df["time"] = pd.to_datetime(df["time"], unit="s")
        return df[["time", "open", "high", "low", "close", "volume"]]

    def market_order(self, symbol, side, volume, sl, tp, magic, comment) -> OrderResult:
        mt5.symbol_select(symbol, True)
        s = mt5.symbol_info(symbol)
        t = mt5.symbol_info_tick(symbol)
        price = t.ask if side == "BUY" else t.bid
        order_type = mt5.ORDER_TYPE_BUY if side == "BUY" else mt5.ORDER_TYPE_SELL
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": symbol, "volume": float(volume),
            "type": order_type, "price": price, "deviation": 20, "magic": magic,
            "comment": comment, "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if sl:
            req["sl"] = float(sl)
        if tp:
            req["tp"] = float(tp)
        r = mt5.order_send(req)
        ok = r.retcode == mt5.TRADE_RETCODE_DONE
        spread_points = (t.ask - t.bid) / s.point if s.point else 0.0
        return OrderResult(ok, r.retcode, _retdesc(r.retcode), r.order or None,
                           r.price or None, r.volume or None, round(spread_points, 1),
                           r._asdict())

    def close_position(self, ticket, volume=None) -> OrderResult:
        pos = next((p for p in (mt5.positions_get(ticket=ticket) or [])), None)
        if pos is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        t = mt5.symbol_info_tick(pos.symbol)
        close_side = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        price = t.bid if pos.type == mt5.POSITION_TYPE_BUY else t.ask
        req = {
            "action": mt5.TRADE_ACTION_DEAL, "symbol": pos.symbol,
            "volume": float(volume or pos.volume), "type": close_side,
            "position": ticket, "price": price, "deviation": 20,
            "magic": pos.magic, "comment": "close", "type_filling": mt5.ORDER_FILLING_IOC,
        }
        r = mt5.order_send(req)
        ok = r.retcode == mt5.TRADE_RETCODE_DONE
        return OrderResult(ok, r.retcode, _retdesc(r.retcode), r.order or None,
                           r.price or None, r.volume or None, None, r._asdict())

    def modify(self, ticket, sl=None, tp=None) -> OrderResult:
        pos = next((p for p in (mt5.positions_get(ticket=ticket) or [])), None)
        if pos is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        req = {
            "action": mt5.TRADE_ACTION_SLTP, "symbol": pos.symbol, "position": ticket,
            "sl": float(sl) if sl is not None else pos.sl,
            "tp": float(tp) if tp is not None else pos.tp,
        }
        r = mt5.order_send(req)
        ok = r.retcode == mt5.TRADE_RETCODE_DONE
        return OrderResult(ok, r.retcode, _retdesc(r.retcode), ticket, None, None, None, r._asdict())


def _retdesc(code: int) -> str:
    if mt5 is None:
        return str(code)
    return {
        getattr(mt5, "TRADE_RETCODE_DONE", 10009): "DONE",
        getattr(mt5, "TRADE_RETCODE_REQUOTE", 10004): "REQUOTE",
        getattr(mt5, "TRADE_RETCODE_REJECT", 10006): "REJECT",
        getattr(mt5, "TRADE_RETCODE_NO_MONEY", 10019): "NO_MONEY",
        getattr(mt5, "TRADE_RETCODE_INVALID_STOPS", 10016): "INVALID_STOPS",
    }.get(code, f"RETCODE_{code}")


# ----------------------------------------------------------------------------
# Simulation backend (deterministic GBM random walk)
# ----------------------------------------------------------------------------
_SIM_SEEDS = {"XAUUSD": (2350.0, 2, 0.01, 0.0008),
              "EURUSD": (1.0850, 5, 0.00001, 0.0004),
              "GBPUSD": (1.2700, 5, 0.00001, 0.0005),
              "USDJPY": (157.50, 3, 0.001, 0.0004)}


def _sim_contract_size(symbol: str) -> float:
    """Contract size (units per 1.0 lot) by instrument class.

    The previous hardcoded FX multiplier of 100,000 was wrong for metals: a
    standard XAUUSD lot is 100 oz, so a $1/oz move on 1.0 lot is $100, not
    $100,000. FX majors keep 100,000 (unchanged), so existing FX simulation
    behavior is preserved.
    """
    s = symbol.upper()
    if s.startswith("XAU"):
        return 100.0        # gold: 100 oz / lot
    if s.startswith("XAG"):
        return 5000.0       # silver: 5,000 oz / lot
    return 100000.0         # FX default (unchanged)


class SimBroker(Broker):
    backend = "simulation"

    def __init__(self, login: int, server: str, leverage: int, balance: float = 100.0,
                 commission_per_lot: float = 0.0, swap_per_lot_per_close: float = 0.0):
        self._login = login or 99999999
        self._server = server or "Exness-Sim"
        self._leverage = leverage
        self._balance = balance
        self._equity = balance
        self._positions: dict[int, dict] = {}
        self._next_ticket = 100001
        self._rng = np.random.default_rng(42)
        # Per-round-turn costs (defaults 0.0 preserve prior cost-free behavior).
        self._commission_per_lot = commission_per_lot
        self._swap_per_lot_per_close = swap_per_lot_per_close
        # Broker-side deal ledger so history() returns realistic IN/OUT deals
        # (with commission/swap and partial/SL-triggered closures) instead of [].
        self._deals: list[dict] = []
        self._next_deal = 900001

    def connect(self) -> bool:
        logger.warning("Using SIMULATION backend (no live MT5 terminal).")
        return True

    def shutdown(self) -> None:
        pass

    def _seed(self, symbol: str):
        return _SIM_SEEDS.get(symbol.upper(), (100.0, 3, 0.001, 0.0005))

    def account_info(self) -> dict:
        floating = sum(self._unrealised(p) for p in self._positions.values())
        self._equity = self._balance + floating
        used_margin = sum(p["volume"] * _sim_contract_size(p["symbol"]) * p["entry_price"] / self._leverage
                          for p in self._positions.values())
        margin_level = (self._equity / used_margin * 100.0) if used_margin else 0.0
        return {
            "login": self._login, "broker": "Exness (Sim)", "server": self._server,
            "mode": "DEMO", "currency": "USD", "leverage": self._leverage,
            "balance": round(self._balance, 2), "equity": round(self._equity, 2),
            "margin": round(used_margin, 2),
            "free_margin": round(self._equity - used_margin, 2),
            "margin_level": round(margin_level, 2),
        }

    def _unrealised(self, p: dict) -> float:
        px = self.tick(p["symbol"])["bid"]
        diff = (px - p["entry_price"]) if p["side"] == "BUY" else (p["entry_price"] - px)
        return diff * p["volume"] * _sim_contract_size(p["symbol"])

    def positions(self) -> list[dict]:
        out = []
        for t, p in self._positions.items():
            q = dict(p)
            q["profit"] = round(self._unrealised(p), 2)
            out.append(q)
        return out

    def history(self, days: int = 7) -> list[dict]:
        to = int(time.time())
        frm = to - days * 86400
        return [d for d in self._deals if d["time"] >= frm]

    def _record_deal(self, p: dict, *, entry: int, volume: float, price: float,
                     profit: float, commission: float, swap: float) -> dict:
        """Append a broker deal (entry=0 IN / entry=1 OUT) mirroring the fields
        MT5Broker.history() exposes, so reconciliation code can be exercised
        offline against the same shape."""
        deal = {
            "ticket": self._next_deal, "order": p["ticket"], "position_id": p["ticket"],
            "symbol": p["symbol"], "type": p["side"], "entry": entry,
            "volume": round(float(volume), 2), "price": price,
            "profit": round(float(profit), 2), "commission": round(float(commission), 2),
            "swap": round(float(swap), 2), "magic": p.get("magic", 0),
            "comment": p.get("comment", ""), "time": int(time.time()),
        }
        self._next_deal += 1
        self._deals.append(deal)
        return deal

    def symbol_info(self, symbol: str) -> dict:
        base, digits, point, vol = self._seed(symbol)
        t = self.tick(symbol)
        spread_points = (t["ask"] - t["bid"]) / point
        contract_size = _sim_contract_size(symbol)
        # Value of one tick_size move per 1.0 lot, in account currency.
        tick_value = contract_size * point
        return {
            "symbol": symbol, "digits": digits, "point": point,
            "bid": t["bid"], "ask": t["ask"], "spread_points": round(spread_points, 1),
            "volume_min": 0.01, "volume_max": 50.0, "volume_step": 0.01,
            "trade_allowed": True, "tick_value": tick_value, "tick_size": point,
            "contract_size": contract_size,
        }

    def tick(self, symbol: str) -> dict:
        base, digits, point, vol = self._seed(symbol)
        # Deterministic intrabar drift from a coarse time bucket
        bucket = int(time.time() // 5)
        noise = math.sin(bucket % 360) * base * vol
        mid = base + noise
        half_spread = point * (15 if symbol.upper().startswith("XAU") else 8)
        return {"symbol": symbol, "bid": round(mid - half_spread, digits),
                "ask": round(mid + half_spread, digits), "time": int(time.time())}

    def ohlc(self, symbol: str, timeframe: str, count: int) -> pd.DataFrame:
        base, digits, point, vol = self._seed(symbol)
        secs = TF_SECONDS.get(timeframe, 60)
        rng = np.random.default_rng(hash((symbol, timeframe)) & 0xFFFFFFFF)
        steps = rng.normal(0, base * vol, size=count).cumsum()
        close = base + steps
        high = close + np.abs(rng.normal(0, base * vol * 0.5, size=count))
        low = close - np.abs(rng.normal(0, base * vol * 0.5, size=count))
        open_ = np.concatenate([[close[0]], close[:-1]])
        now = int(time.time())
        times = pd.to_datetime([now - (count - i) * secs for i in range(count)], unit="s")
        return pd.DataFrame({
            "time": times, "open": open_.round(digits), "high": high.round(digits),
            "low": low.round(digits), "close": close.round(digits),
            "volume": rng.integers(50, 500, size=count),
        })

    def market_order(self, symbol, side, volume, sl, tp, magic, comment) -> OrderResult:
        info = self.symbol_info(symbol)
        price = info["ask"] if side == "BUY" else info["bid"]
        ticket = self._next_ticket
        self._next_ticket += 1
        self._positions[ticket] = {
            "ticket": ticket, "symbol": symbol, "side": side, "volume": float(volume),
            "entry_price": price, "sl": sl, "tp": tp, "swap": 0.0, "magic": magic,
            "comment": comment,
        }
        entry_commission = -self._commission_per_lot * float(volume)
        self._balance += entry_commission
        self._record_deal(self._positions[ticket], entry=0, volume=float(volume), price=price,
                          profit=0.0, commission=entry_commission, swap=0.0)
        logger.info("[SIM] opened {} {} {} @ {} ticket={}", side, volume, symbol, price, ticket)
        return OrderResult(True, 10009, "DONE", ticket, price, float(volume),
                           info["spread_points"], {"sim": True})

    def _close_at(self, ticket: int, close_price: float, volume=None, reason: str = "close") -> OrderResult:
        """Shared close path for both a normal close and a broker SL trigger;
        records an OUT deal (with commission+swap) and updates balance by the
        realized amount (price P&L net of costs)."""
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        full_vol = p["volume"]
        vol = float(volume or full_vol)
        diff = (close_price - p["entry_price"]) if p["side"] == "BUY" else (p["entry_price"] - close_price)
        price_pnl = diff * vol * _sim_contract_size(p["symbol"])
        commission = -self._commission_per_lot * vol
        swap = -self._swap_per_lot_per_close * vol
        self._balance += price_pnl + commission + swap
        p_snapshot = dict(p, comment=reason if reason else p.get("comment", ""))
        if vol >= full_vol:
            del self._positions[ticket]
        else:  # partial close: residual stays open
            p["volume"] = round(full_vol - vol, 8)
        self._record_deal(p_snapshot, entry=1, volume=vol, price=close_price,
                          profit=price_pnl, commission=commission, swap=swap)
        logger.info("[SIM] closed ticket={} vol={} realized≈{:.2f} ({})",
                    ticket, vol, price_pnl + commission + swap, reason)
        return OrderResult(True, 10009, "DONE", ticket, close_price, vol, None, {"sim": True})

    def close_position(self, ticket, volume=None) -> OrderResult:
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        info = self.symbol_info(p["symbol"])
        price = info["bid"] if p["side"] == "BUY" else info["ask"]
        return self._close_at(ticket, price, volume=volume, reason=p.get("comment", "close"))

    def force_sl_close(self, ticket: int) -> OrderResult:
        """Simulate a BROKER-SIDE stop-loss firing: the position is closed at
        its SL price and removed, so a subsequent client ``close_position`` sees
        'position not found' while ``history()`` shows the OUT deal — exactly the
        live10 reconciliation scenario (P0-C)."""
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        if p.get("sl") in (None, 0, 0.0):
            return OrderResult(False, -1, "no stop loss attached", None, None, None, None, {})
        return self._close_at(ticket, float(p["sl"]), volume=None, reason="sl")

    def modify(self, ticket, sl=None, tp=None) -> OrderResult:
        p = self._positions.get(ticket)
        if p is None:
            return OrderResult(False, -1, "position not found", None, None, None, None, {})
        if sl is not None:
            p["sl"] = sl
        if tp is not None:
            p["tp"] = tp
        return OrderResult(True, 10009, "DONE", ticket, None, None, None, {"sim": True})


def build_broker(settings) -> Broker:
    """Factory: real MT5 if reachable, else simulation."""
    if MT5_AVAILABLE and settings.mt5_login and settings.mt5_password and settings.mt5_server:
        conn = MT5Connection(settings.mt5_login, settings.mt5_password,
                             settings.mt5_server, settings.mt5_path)
        broker = MT5Broker(conn)
        if broker.connect():
            ok, problems = conn.verify(settings.leverage, settings.account_mode)
            if not ok:
                logger.error("Verification problems: {}", problems)
            return broker
        logger.error("MT5 connect failed — falling back to simulation.")
    return SimBroker(settings.mt5_login or 0, settings.mt5_server or "",
                     settings.leverage)
