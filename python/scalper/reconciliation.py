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


# ---------------------------------------------------------------------------
# Step 2: authoritative broker-deal reconciliation
# ---------------------------------------------------------------------------

# reconcile_close statuses
STATUS_CONFIRMED = "CONFIRMED"        # OUT deal(s) present -> realized P&L is known
STATUS_STILL_OPEN = "STILL_OPEN"      # position still open, no new OUT deal
STATUS_TRULY_ABSENT = "TRULY_ABSENT"  # gone, but no OUT deal evidence -> outcome UNKNOWN
STATUS_TRANSIENT = "TRANSIENT"        # broker query failed -> retry, do not decide


def _deal_cost_sum(deals: list[dict]) -> float:
    """Realized account impact of a set of deals = Σ(profit + commission + swap).

    IN deals (entry) carry profit 0 but may carry entry-side commission, so
    summing over IN+OUT correctly allocates entry trading costs."""
    return round(sum(float(d.get("profit", 0.0)) + float(d.get("commission", 0.0))
                     + float(d.get("swap", 0.0)) for d in deals), 2)


class ReconcileLedger:
    """Records already-booked broker deal tickets so the same deal is never
    counted twice — across repeated reconciliation, duplicate history replays
    or a runner restart. Serializable (``processed``) for restart safety."""

    def __init__(self, processed: list | set | None = None):
        self._processed: set = set(processed or [])

    def unbooked(self, position_id: int, deals: list[dict]) -> list[dict]:
        return [d for d in deals
                if d.get("position_id") == position_id and d.get("ticket") not in self._processed]

    def book(self, deals: list[dict]) -> None:
        for d in deals:
            self._processed.add(d.get("ticket"))

    def book_tickets(self, tickets: list) -> None:
        for t in tickets:
            self._processed.add(t)

    @property
    def processed(self) -> set:
        return set(self._processed)


@dataclass(frozen=True)
class CloseReconciliation:
    status: str
    realized: float | None          # None unless CONFIRMED — never 0 for an unknown outcome
    state_known: bool               # False => block new entries (fail closed)
    closed_by_broker: bool          # True when the client close failed but deals confirm a close
    booked_deal_tickets: list       # OUT/IN deal tickets newly counted (for the ledger)
    detail: str


def reconcile_close(position_id: int, positions: list[dict] | None, deals: list[dict] | None,
                    ledger: ReconcileLedger, *, client_close_ok: bool = True) -> CloseReconciliation:
    """Determine the authoritative outcome of a close from broker evidence.

    ``positions``/``deals`` are snapshots already fetched from the broker; pass
    ``None`` for either when that query FAILED (disconnect/timeout) — this
    yields TRANSIENT (retry), never a fabricated zero-P&L close.
    ``client_close_ok`` is whether ``close_position`` itself returned success;
    a False with confirming OUT deals means the broker closed it (e.g. SL).
    """
    if positions is None or deals is None:
        return CloseReconciliation(STATUS_TRANSIENT, None, False, False, [],
                                   "broker query failed (positions/deals unavailable) — retry")

    still_open = any(p.get("ticket") == position_id for p in positions)
    unbooked = ledger.unbooked(position_id, deals)
    out_deals = [d for d in unbooked if d.get("entry") == 1]

    if out_deals:
        realized = _deal_cost_sum(unbooked)   # includes entry-side costs still unbooked
        return CloseReconciliation(
            STATUS_CONFIRMED, realized, True, (not client_close_ok),
            [d.get("ticket") for d in unbooked],
            f"{len(out_deals)} OUT deal(s) confirm close; realized={realized}")

    if still_open:
        return CloseReconciliation(STATUS_STILL_OPEN, None, True, False, [],
                                   "position still open, no new OUT deal")

    # Not open and no OUT deal evidence: outcome genuinely unknown. Do NOT book 0.
    return CloseReconciliation(STATUS_TRULY_ABSENT, None, False, False, [],
                               "position absent with no OUT deal — outcome unknown, state not trusted")


def reconcile_with_retry(position_id, fetch_positions, fetch_deals, ledger, *,
                         client_close_ok: bool = True, max_attempts: int = 5):
    """Poll broker snapshots up to ``max_attempts`` times until a close is
    confirmed; ``fetch_*`` raise or return None on a failed query (TRANSIENT).
    Pure w.r.t. time (no sleeps) — the caller injects fetch callables, so this
    is fully offline-testable. Returns the final CloseReconciliation."""
    last = None
    for _ in range(max_attempts):
        try:
            positions = fetch_positions()
            deals = fetch_deals()
        except Exception:
            positions = deals = None
        last = reconcile_close(position_id, positions, deals, ledger, client_close_ok=client_close_ok)
        if last.status in (STATUS_CONFIRMED, STATUS_STILL_OPEN):
            return last
    return last
