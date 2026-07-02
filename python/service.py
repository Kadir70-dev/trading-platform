"""FastAPI service: the single owner of the MT5 connection and the strategy.

The Go orchestrator and the CLI talk to this over HTTP. Keeping the broker
connection in one process avoids MT5's single-terminal-per-process constraint
and keeps the Wine boundary in exactly one place.

Run:  uvicorn service:app --host 0.0.0.0 --port 8800
"""
from __future__ import annotations

import sys
import time

import pandas as pd
from fastapi import FastAPI, HTTPException
from loguru import logger
from pydantic import BaseModel

from config import get_settings
from mt5.client import build_broker
from strategies.ema_rsi import StrategyParams
from strategies.signal_generator import SignalGenerator

settings = get_settings()
logger.remove()
logger.add(sys.stderr, level=settings.log_level)
logger.add(f"{settings.log_dir}/python_service.log", rotation="20 MB",
           retention="14 days", level=settings.log_level, enqueue=True)

app = FastAPI(title="Exness MT5 Trading — Python Service", version="1.0.0")

broker = build_broker(settings)
sig_params = StrategyParams(
    ema_fast=settings.ema_fast, ema_slow=settings.ema_slow,
    rsi_period=settings.rsi_period, rsi_buy_level=settings.rsi_buy_level,
    rsi_sell_level=settings.rsi_sell_level, sl_atr_mult=settings.sl_atr_mult,
    tp_rr=settings.tp_rr,
)
siggen = SignalGenerator(sig_params)


# ---- request models ----
class SignalRequest(BaseModel):
    symbol: str
    timeframe: str = "M1"
    count: int = 200


class MarketOrderRequest(BaseModel):
    symbol: str
    side: str                       # BUY | SELL
    volume: float
    sl: float | None = None
    tp: float | None = None
    comment: str = "auto"


class CloseRequest(BaseModel):
    ticket: int
    volume: float | None = None     # partial close if < position volume


class ModifyRequest(BaseModel):
    ticket: int
    sl: float | None = None
    tp: float | None = None


# ---- endpoints ----
@app.get("/health")
def health():
    return {"status": "ok", "backend": broker.backend, "ts": int(time.time())}


@app.get("/account")
def account():
    return broker.account_info()


@app.get("/positions")
def positions():
    return {"positions": broker.positions()}


@app.get("/history")
def history(days: int = 7):
    return {"deals": broker.history(days)}


@app.get("/symbol/{symbol}")
def symbol_info(symbol: str):
    return broker.symbol_info(symbol.upper())


@app.get("/ohlc")
def ohlc(symbol: str, timeframe: str = "M1", count: int = 200):
    df = broker.ohlc(symbol.upper(), timeframe.upper(), count)
    return {"symbol": symbol.upper(), "timeframe": timeframe.upper(),
            "bars": df.assign(time=df["time"].astype(str)).to_dict(orient="records")}


@app.post("/signal")
def signal(req: SignalRequest):
    df = broker.ohlc(req.symbol.upper(), req.timeframe.upper(), req.count)
    if df.empty:
        raise HTTPException(503, "no market data")
    sig = siggen.generate(req.symbol.upper(), req.timeframe.upper(), df)
    return sig.to_dict()


@app.post("/order/market")
def market_order(req: MarketOrderRequest):
    t0 = time.perf_counter()
    res = broker.market_order(req.symbol.upper(), req.side.upper(), req.volume,
                              req.sl, req.tp, settings.magic_number, req.comment)
    latency_ms = round((time.perf_counter() - t0) * 1000, 3)
    return {**res.__dict__, "latency_ms": latency_ms}


@app.post("/order/close")
def close(req: CloseRequest):
    res = broker.close_position(req.ticket, req.volume)
    return res.__dict__


@app.post("/order/close-all")
def close_all():
    results = []
    for p in broker.positions():
        results.append(broker.close_position(p["ticket"]).__dict__)
    return {"closed": len(results), "results": results}


@app.post("/order/modify")
def modify(req: ModifyRequest):
    res = broker.modify(req.ticket, req.sl, req.tp)
    return res.__dict__


if __name__ == "__main__":
    import uvicorn
    host, port = settings.python_service_addr.split(":")
    uvicorn.run("service:app", host=host, port=int(port), reload=False)
