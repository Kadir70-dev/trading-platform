"""Centralised settings, loaded from environment / .env.

Single source of truth for the Python service. Values mirror the root .env so
the Python, Go and C++ processes all agree on the same configuration.
"""
from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # Broker / account
    broker: str = Field("EXNESS", alias="BROKER")
    account_mode: str = Field("DEMO", alias="ACCOUNT_MODE")
    mt5_login: int | None = Field(None, alias="MT5_LOGIN")
    mt5_password: str | None = Field(None, alias="MT5_PASSWORD")
    mt5_server: str | None = Field(None, alias="MT5_SERVER")
    mt5_path: str | None = Field(None, alias="MT5_PATH")
    leverage: int = Field(2000, alias="LEVERAGE")

    # Trading
    symbols_raw: str = Field("XAUUSD,EURUSD,GBPUSD,USDJPY", alias="SYMBOLS")
    default_symbol: str = Field("XAUUSD", alias="DEFAULT_SYMBOL")
    timeframe: str = Field("M1", alias="TIMEFRAME")
    default_lot: float = Field(0.01, alias="DEFAULT_LOT")
    magic_number: int = Field(20260630, alias="MAGIC_NUMBER")

    # Risk
    max_risk_pct: float = Field(1.0, alias="MAX_RISK_PCT")
    min_rr: float = Field(2.0, alias="MIN_RR")
    max_spread_points: int = Field(50, alias="MAX_SPREAD_POINTS")

    # Strategy
    ema_fast: int = Field(20, alias="EMA_FAST")
    ema_slow: int = Field(50, alias="EMA_SLOW")
    rsi_period: int = Field(14, alias="RSI_PERIOD")
    rsi_buy_level: float = Field(50, alias="RSI_BUY_LEVEL")
    rsi_sell_level: float = Field(50, alias="RSI_SELL_LEVEL")
    sl_atr_mult: float = Field(1.5, alias="SL_ATR_MULT")
    tp_rr: float = Field(2.0, alias="TP_RR")

    # Service
    python_service_addr: str = Field("127.0.0.1:8800", alias="PYTHON_SERVICE_ADDR")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    log_dir: str = Field("./logs", alias="LOG_DIR")

    @property
    def symbols(self) -> List[str]:
        return [s.strip().upper() for s in self.symbols_raw.split(",") if s.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
