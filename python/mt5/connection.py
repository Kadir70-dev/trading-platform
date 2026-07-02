"""MT5 terminal connection lifecycle + verification.

Owns initialize / login / shutdown and the startup verification checklist:
login, server, broker, leverage and trading permission. The MetaTrader5
package ships a Windows binary; on Ubuntu it runs under Wine (see docs). If the
package or terminal is unavailable, callers should fall back to the simulation
backend in client.py.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from loguru import logger

try:
    import MetaTrader5 as mt5  # type: ignore
    MT5_AVAILABLE = True
except Exception as exc:  # pragma: no cover - platform dependent
    mt5 = None  # type: ignore
    MT5_AVAILABLE = False
    logger.warning("MetaTrader5 package unavailable: {} — simulation backend will be used", exc)


@dataclass
class AccountIdentity:
    login: int
    broker: str
    server: str
    mode: str           # DEMO | REAL
    currency: str
    leverage: int
    trade_allowed: bool


class MT5Connection:
    def __init__(self, login: int, password: str, server: str, path: Optional[str] = None):
        self.login = login
        self.password = password
        self.server = server
        self.path = path
        self._connected = False

    @property
    def available(self) -> bool:
        return MT5_AVAILABLE

    def connect(self) -> bool:
        if not MT5_AVAILABLE:
            return False
        kwargs = {"login": self.login, "password": self.password, "server": self.server}
        if self.path:
            kwargs["path"] = self.path
        if not mt5.initialize(**kwargs):
            logger.error("mt5.initialize failed: {}", mt5.last_error())
            return False
        if not mt5.login(self.login, password=self.password, server=self.server):
            logger.error("mt5.login failed: {}", mt5.last_error())
            mt5.shutdown()
            return False
        self._connected = True
        logger.success("Connected to MT5 {} on {}", self.login, self.server)
        return True

    def shutdown(self) -> None:
        if MT5_AVAILABLE and self._connected:
            mt5.shutdown()
            self._connected = False

    def identity(self) -> Optional[AccountIdentity]:
        if not (MT5_AVAILABLE and self._connected):
            return None
        info = mt5.account_info()
        term = mt5.terminal_info()
        if info is None:
            logger.error("account_info() returned None: {}", mt5.last_error())
            return None
        mode = "DEMO" if info.trade_mode == mt5.ACCOUNT_TRADE_MODE_DEMO else "REAL"
        return AccountIdentity(
            login=info.login,
            broker=getattr(info, "company", "UNKNOWN"),
            server=info.server,
            mode=mode,
            currency=info.currency,
            leverage=info.leverage,
            trade_allowed=bool(getattr(term, "trade_allowed", True)) and info.trade_allowed != 0,
        )

    def verify(self, expected_leverage: int, expected_mode: str) -> tuple[bool, list[str]]:
        """Run the startup verification checklist. Returns (ok, problems)."""
        problems: list[str] = []
        ident = self.identity()
        if ident is None:
            return False, ["could not read account identity"]

        if not ident.trade_allowed:
            problems.append("trading not allowed on this account/terminal")
        if expected_mode and ident.mode != expected_mode.upper():
            problems.append(f"account mode {ident.mode} != expected {expected_mode.upper()}")
        if expected_leverage and ident.leverage != expected_leverage:
            # Not fatal: brokers may cap; we log loudly but continue.
            logger.warning("Leverage mismatch: broker={} expected={}",
                           ident.leverage, expected_leverage)

        logger.info("Broker: {}", ident.broker)
        logger.info("Server: {}", ident.server)
        logger.info("Account: {} ({})", ident.login, ident.mode)
        logger.info("Leverage: 1:{}", ident.leverage)
        ok = len(problems) == 0
        return ok, problems
