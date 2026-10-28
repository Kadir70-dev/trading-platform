"""Telegram notifications (optional, fail-safe).

Credentials come ONLY from the environment (SCALPER_TELEGRAM_BOT_TOKEN,
SCALPER_TELEGRAM_CHAT_ID) — never from source control. A send failure is
swallowed and recorded; notifications must never crash or block trading.

The transport is injectable so delivery is fully offline-testable without any
network. Realized-P&L messages must be passed BROKER-RECONCILED values; this
module labels estimates and confirmed figures distinctly and never fabricates.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional
from urllib import error, parse, request


def _default_transport(token: str, chat_id: str, text: str, timeout: float = 10.0) -> bool:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text}).encode()
    try:
        with request.urlopen(request.Request(url, data=data), timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (error.URLError, OSError, ValueError):
        return False


@dataclass
class TelegramNotifier:
    token: Optional[str] = None
    chat_id: Optional[str] = None
    transport: Callable[[str, str, str], bool] = _default_transport
    enabled: bool = True
    sent: list = field(default_factory=list)      # delivered messages (for tests/audit)
    failures: int = 0

    @classmethod
    def from_env(cls, transport: Callable[[str, str, str], bool] | None = None) -> "TelegramNotifier":
        token = os.getenv("SCALPER_TELEGRAM_BOT_TOKEN")
        chat = os.getenv("SCALPER_TELEGRAM_CHAT_ID")
        return cls(token=token, chat_id=chat,
                   transport=transport or _default_transport,
                   enabled=bool(token and chat))

    def send(self, text: str) -> bool:
        """Best-effort send. Returns True on delivery; never raises."""
        if not self.enabled or not self.token or not self.chat_id:
            return False
        try:
            ok = bool(self.transport(self.token, self.chat_id, text))
        except Exception:
            ok = False
        if ok:
            self.sent.append(text)
        else:
            self.failures += 1
        return ok

    # --- typed events -------------------------------------------------------
    def startup(self, symbol, mode, balance):
        return self.send(f"🟢 Scalper STARTED {symbol} [{mode}] balance={balance}")

    def shutdown(self, reason=""):
        return self.send(f"⚪ Scalper STOPPED {reason}".strip())

    def broker_connection(self, connected: bool):
        return self.send("🔌 MT5 CONNECTED" if connected else "⚠️ MT5 DISCONNECTED")

    def signal(self, side, score=None):
        return self.send(f"📶 SIGNAL {side}" + (f" score={score}" if score is not None else ""))

    def order_executed(self, side, lot, price, ticket):
        return self.send(f"✅ ORDER {side} {lot}@{price} ticket={ticket}")

    def order_rejected(self, reason):
        return self.send(f"❌ ORDER REJECTED: {reason}")

    def position_closed(self, ticket, realized, confirmed: bool):
        """`confirmed` must be True ONLY for broker-reconciled realized P&L."""
        label = "realized(broker-confirmed)" if confirmed else "UNCONFIRMED"
        amount = "unknown" if realized is None else f"{realized:+.2f}"
        return self.send(f"📕 CLOSED ticket={ticket} {label}={amount}")

    def daily_summary(self, realized, trades, confirmed: bool = True):
        label = "broker-confirmed" if confirmed else "estimate"
        return self.send(f"📊 DAILY P&L ({label}) {realized:+.2f} over {trades} trades")

    def daily_loss_limit(self, realized):
        return self.send(f"🛑 DAILY LOSS LIMIT reached ({realized:+.2f}) — trading halted")

    def emergency_stop(self, reason):
        return self.send(f"🚨 EMERGENCY STOP: {reason}")

    def manual_intervention(self, reason):
        return self.send(f"🧰 MANUAL INTERVENTION REQUIRED: {reason}")
