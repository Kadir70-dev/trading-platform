"""Thin adapter from the scalper's fixed-lot LONG/SHORT decisions onto the
existing mt5.client.Broker interface. Live-demo runner only — the backtester
never touches a Broker instance, it replays historical bars directly.

The state machine decides *when* to exit on its own judgment (let winners
run/trail, cut losers on confirmed reversal), so orders are sent without a
broker-side TP. A broker-side emergency SL is still attached, at the same
`emergency_stop_r_mult * risk_unit` distance as exit.py's own catastrophic
backstop check, so an open position is never truly unbounded if this process
dies mid-trade or a gap moves faster than the Python-side check can react.
"""
from __future__ import annotations

from mt5.client import Broker, OrderResult

from .config import ScalperParams
from .types import PositionState


class BrokerAdapter:
    def __init__(self, broker: Broker, params: ScalperParams):
        self.broker = broker
        self.p = params

    def open(self, direction: PositionState, price: float, risk_unit: float, comment: str,
             volume: float | None = None) -> OrderResult:
        """`volume` overrides the fixed `lot_size` when the runner supplies a
        risk-sized lot (Step 3). The SL distance is unchanged — the emergency
        stop is a PRICE distance (`risk_unit * emergency_stop_r_mult`),
        independent of lot size, so overriding volume never changes the stop
        level or any strategy decision, only how many lots carry it."""
        side = "BUY" if direction == PositionState.LONG else "SELL"
        safety_dist = risk_unit * self.p.emergency_stop_r_mult
        sl = price - safety_dist if direction == PositionState.LONG else price + safety_dist
        lot = self.p.lot_size if volume is None else volume
        return self.broker.market_order(self.p.symbol, side, lot,
                                         sl=sl, tp=None, magic=self.p.magic_number, comment=comment)

    def close(self, ticket: int) -> OrderResult:
        return self.broker.close_position(ticket)
