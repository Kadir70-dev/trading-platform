"""Broker-truth reconciliation helpers (pure, side-effect-free).

Covers the decision logic for: fail-closed trading when broker financial state
is unknown, startup adopt/flatten of a pre-existing position, duplicate-order
detection, orphan (no-SL) detection, classifying a close against broker history
(the live10 'position not found' vs 'closed by broker' case), and computing
AUTHORITATIVE realized P&L from broker deals (profit + commission + swap) or
from a broker balance sequence.

STEP 1 STATUS: pure helpers, NOT wired into run_scalper_demo.py or risk_guards
(that is Steps 4/7 / P1-4 and P1-7). Nothing here opens, closes or modifies a
position; it only computes decisions and figures from data passed in.

Design note (fail-closed): ``trade_gate`` defaults to BLOCKING when broker
state is unknown. There is no enabled fail-open default anywhere in this
module.
"""
from __future__ import annotations

from dataclasses import dataclass

# Close classifications
CLOSE_OPEN = "OPEN"                        # still open per positions snapshot
CLOSE_BY_BROKER = "CLOSED_BY_BROKER"       # gone from positions, OUT deal exists (e.g. SL fired)
CLOSE_TRULY_ABSENT = "TRULY_ABSENT"        # gone from positions, no OUT deal (genuine error)


@dataclass(frozen=True)
class GateResult:
    ok: bool
    reason: str


def trade_gate(*, broker_state_known: bool) -> GateResult:
    """Fail CLOSED: no new entries while broker financial state is unknown."""
    if not broker_state_known:
        return GateResult(False, "BLOCKED_STATE_UNKNOWN")
    return GateResult(True, "STATE_KNOWN")


def _mine(positions: list[dict], magic: int, symbol: str) -> list[dict]:
    return [p for p in positions
            if p.get("magic") == magic and p.get("symbol") == symbol]


def startup_decision(positions: list[dict], magic: int, symbol: str,
                     policy: str = "flatten") -> dict:
    """On process (re)start, decide what to do with any pre-existing magic
    position. policy='flatten' (default, safest) or 'adopt'."""
    mine = _mine(positions, magic, symbol)
    if not mine:
        return {"action": "START_FLAT", "positions": []}
    if policy == "adopt":
        return {"action": "ADOPT", "positions": mine}
    return {"action": "FLATTEN", "positions": mine}


def would_duplicate(positions: list[dict], magic: int, symbol: str, bar_tag: str) -> bool:
    """True if a magic position already carries this bar's client tag — the
    guard against double-opening the same bar after a retry/restart."""
    return any(p.get("magic") == magic and p.get("symbol") == symbol
               and str(p.get("comment", "")).endswith(bar_tag)
               for p in positions)


def positions_without_sl(positions: list[dict], magic: int, symbol: str) -> list[dict]:
    """Magic positions missing a broker-side SL (orphans needing fail-closed
    protection)."""
    return [p for p in _mine(positions, magic, symbol)
            if not p.get("sl")]


def classify_close(ticket: int, positions: list[dict], deals: list[dict]) -> str:
    """Classify a close before trusting a 'position not found' as success.
    TRANSIENT query failures (positions/deals is None) are the caller's concern
    (retry); this function assumes both snapshots were successfully read."""
    if any(p.get("ticket") == ticket for p in positions):
        return CLOSE_OPEN
    outs = [d for d in deals if d.get("position_id") == ticket and d.get("entry") == 1]
    return CLOSE_BY_BROKER if outs else CLOSE_TRULY_ABSENT


def realized_for_position(ticket: int, deals: list[dict]) -> dict:
    """AUTHORITATIVE realized P&L for a position = sum over its OUT deals of
    (profit + commission + swap). Handles partial fills (multiple OUT deals)."""
    outs = [d for d in deals if d.get("position_id") == ticket and d.get("entry") == 1]
    realized = sum(float(d.get("profit", 0.0)) + float(d.get("commission", 0.0))
                   + float(d.get("swap", 0.0)) for d in outs)
    volume = sum(float(d.get("volume", 0.0)) for d in outs)
    return {"realized": round(realized, 2), "out_deals": len(outs),
            "volume": round(volume, 8), "found": bool(outs)}


def realized_from_balance_sequence(close_balances: list[float], start_balance: float) -> list[float]:
    """Per-trade realized derived from the broker's own balance after each
    close — the balance delta is broker-sourced truth, independent of any
    strategy-computed P&L."""
    out: list[float] = []
    prev = start_balance
    for b in close_balances:
        out.append(round(b - prev, 2))
        prev = b
    return out
