"""Read-only M1 clock reconstruction for pre-existing broker positions.

All inputs come from broker positions, deal history and actual observed M1
candles. This module NEVER sends an order or modifies a position.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal


@dataclass(frozen=True)
class Deal:
    position_id: int
    order_id: int
    entry: Literal["IN", "OUT", "INOUT", "OUT_BY"]
    time_msc: int


@dataclass(frozen=True)
class RecoveryResult:
    status: Literal["ADOPT", "MANUAL_INTERVENTION"]
    reason: str
    opened_at_msc: int | None = None
    bars_held: int | None = None
    entry_bar_index: int | None = None


def recover_m1_clock(*, position_id: int, position_time_msc: int | None,
                     deals: Iterable[Deal] | None, m1_bar_opens_sec: Iterable[int] | None,
                     current_bar_open_sec: int, current_bar_index: int,
                     history_available: bool = True,
                     allowable_clock_discrepancy_msc: int = 60_000) -> RecoveryResult:
    """Reconstruct holding duration from actual broker M1 bars, not wall time.

    The M1 series must include the entry candle and most recent CLOSED candle.
    Unknown, incomplete or inconsistent evidence blocks automatic adoption.
    """
    def blocked(reason: str) -> RecoveryResult:
        return RecoveryResult("MANUAL_INTERVENTION", reason)

    if position_id <= 0 or current_bar_index < 0 or current_bar_open_sec <= 0:
        return blocked("INVALID_ID_OR_CURRENT_BAR")
    if not history_available or deals is None:
        return blocked("BROKER_HISTORY_UNAVAILABLE")
    if m1_bar_opens_sec is None:
        return blocked("BROKER_M1_HISTORY_UNAVAILABLE")

    all_deals = list(deals)
    if any(d.position_id <= 0 or d.time_msc <= 0 or d.order_id <= 0 for d in all_deals):
        return blocked("INVALID_DEAL_METADATA")
    relevant = [d for d in all_deals if d.position_id == position_id]
    if any(d.entry == "INOUT" for d in relevant):
        return blocked("NETTING_REVERSAL_UNSUPPORTED")
    entries = sorted((d for d in relevant if d.entry == "IN"), key=lambda d: d.time_msc)
    if len({d.order_id for d in entries}) > 1:
        return blocked("MULTIPLE_ENTRY_ORDERS_REQUIRE_MANUAL_REVIEW")
    first_deal_msc = entries[0].time_msc if entries else None

    if position_time_msc is not None and position_time_msc <= 0:
        return blocked("INVALID_POSITION_OPEN_TIMESTAMP")
    if first_deal_msc is not None and position_time_msc is not None:
        if abs(first_deal_msc - position_time_msc) > allowable_clock_discrepancy_msc:
            return blocked("POSITION_VS_ENTRY_DEAL_TIME_CONFLICT")
    opened_msc = first_deal_msc if first_deal_msc is not None else position_time_msc
    if opened_msc is None:
        return blocked("OPEN_TIMESTAMP_NOT_ESTABLISHED")
    if opened_msc > current_bar_open_sec * 1000 + 59_999:
        return blocked("POSITION_OPENS_AFTER_CURRENT_BAR")

    bars = list(m1_bar_opens_sec)
    if not bars or bars != sorted(set(bars)):
        return blocked("M1_BARS_MISSING_DUPLICATE_OR_UNSORTED")
    if any(b <= 0 or b % 60 != 0 for b in bars):
        return blocked("INVALID_M1_BAR_OPEN_TIME")
    opened_bar_sec = (opened_msc // 60_000) * 60
    if bars[0] != opened_bar_sec or bars[-1] != current_bar_open_sec:
        return blocked("M1_HISTORY_DOES_NOT_SPAN_ENTRY_TO_CURRENT")
    if opened_bar_sec > current_bar_open_sec:
        return blocked("BROKER_BAR_CLOCK_INVALID")

    # Count actual observed post-entry candles. No phantom weekend bars.
    held = len(bars) - 1
    return RecoveryResult("ADOPT", "BROKER_TIME_AND_M1_BARS_RECONCILED",
                          opened_at_msc=opened_msc, bars_held=held,
                          entry_bar_index=current_bar_index - held)
