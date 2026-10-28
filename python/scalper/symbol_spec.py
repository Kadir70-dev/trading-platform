"""Broker symbol specification — a typed, test-friendly view over the dict that
``Broker.symbol_info()`` returns, with the point/tick/contract conversions the
scalper needs for correct sizing and spread math.

STEP 1 STATUS: pure helper, NOT yet wired into the live runner, broker_adapter
or state machine (that is Step 5 / P1-5). Importing or editing this module does
not change any production execution path.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SymbolSpec:
    symbol: str
    point: float
    digits: int
    tick_size: float
    tick_value: float      # account-currency value of one tick_size move, per 1.0 lot
    contract_size: float   # units per 1.0 lot (XAU=100 oz, FX major=100_000)
    volume_min: float
    volume_max: float
    volume_step: float

    @classmethod
    def from_symbol_info(cls, info: dict) -> "SymbolSpec":
        point = float(info["point"])
        tick_size = float(info.get("tick_size", point)) or point
        return cls(
            symbol=info["symbol"],
            point=point,
            digits=int(info.get("digits", 2)),
            tick_size=tick_size,
            tick_value=float(info.get("tick_value", 0.0)),
            contract_size=float(info.get("contract_size", 0.0)),
            volume_min=float(info.get("volume_min", 0.01)),
            volume_max=float(info.get("volume_max", 100.0)),
            volume_step=float(info.get("volume_step", 0.01)),
        )

    def to_points(self, price_distance: float) -> float:
        """Convert a price distance ($/oz for XAU) into broker 'points'."""
        return price_distance / self.point

    def from_points(self, points: float) -> float:
        return points * self.point

    def value_per_lot(self, price_distance: float) -> float:
        """Account-currency P&L of a price move of ``price_distance`` on 1.0 lot.

        Uses tick_value/tick_size (broker-reported), not an assumed $100/oz.
        For XAUUSD (tick_size 0.01, tick_value 1.0): a $1.00/oz move → $100/lot.
        """
        if self.tick_size <= 0:
            return 0.0
        return (price_distance / self.tick_size) * self.tick_value
