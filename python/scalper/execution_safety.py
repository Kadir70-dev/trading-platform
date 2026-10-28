"""Step 3 production-safety composition layer.

Wires the existing pure modules — symbol_spec, sizing, reconciliation — into
broker-execution preflight, risk-based lot planning, restart recovery and a
fail-closed entry gate. The trading STRATEGY (direction/entry/exit/flip/trail)
is untouched: nothing here influences a BUY/SELL/EXIT decision; it only governs
whether and at what *size* an already-decided entry may be executed.

Everything is driven by a broker object implementing the mt5.client.Broker
interface, so it is fully offline-testable with SimBroker. No orders are placed
here; no positions are closed or modified.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .sizing import SizingResult, compute_lot
from .symbol_spec import SymbolSpec
from .types import Position, PositionState

# ---------------------------------------------------------------------------
# Phase A — symbol specifications / preflight
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PreflightResult:
    ok: bool
    reason: str
    spec: SymbolSpec | None


def load_symbol_spec(broker, symbol: str) -> SymbolSpec | None:
    """Fetch and validate the broker's instrument spec. Returns None if the
    spec cannot be verified (query failed, missing/nonsensical fields) — the
    caller must then BLOCK new entries (no assumed point size)."""
    try:
        info = broker.symbol_info(symbol)
    except Exception:
        return None
    if not info:
        return None
    required = ("point", "tick_size", "tick_value", "contract_size",
                "volume_min", "volume_max", "volume_step")
    for k in required:
        v = info.get(k)
        if v is None or (isinstance(v, (int, float)) and v <= 0):
            return None
    return SymbolSpec.from_symbol_info(info)


def preflight(broker, symbol: str, *, expect_mode: str = "DEMO",
              ident_mode: str | None = None, trade_allowed: bool | None = None) -> PreflightResult:
    """Block unless the account mode matches, the symbol is tradeable, and the
    broker spec is verifiable. Unverifiable spec => entries blocked."""
    if ident_mode is not None and ident_mode != expect_mode:
        return PreflightResult(False, f"MODE_MISMATCH(expected {expect_mode}, got {ident_mode})", None)
    if trade_allowed is False:
        return PreflightResult(False, "SYMBOL_NOT_TRADEABLE", None)
    spec = load_symbol_spec(broker, symbol)
    if spec is None:
        return PreflightResult(False, "SPEC_UNVERIFIED", None)
    return PreflightResult(True, "OK", spec)


# ---------------------------------------------------------------------------
# Phase B — dynamic lot sizing
# ---------------------------------------------------------------------------
def plan_entry_lot(equity: float, risk_pct: float, spec: SymbolSpec, params, *,
                   price: float | None = None, free_margin: float | None = None,
                   leverage: int | None = None, broker_min_stop_usd: float | None = None) -> SizingResult:
    """Risk-size an entry from the REAL protective-stop distance and broker
    contract economics. Stop distance = the strategy's own emergency stop
    (`emergency_stop_r_mult * stop_distance_usd`), widened to the broker minimum
    stop if that is larger. Never rounds up to the min lot past the risk budget
    (compute_lot rounds down and rejects below-min)."""
    stop_distance = params.emergency_stop_r_mult * params.stop_distance_usd
    if broker_min_stop_usd:
        stop_distance = max(stop_distance, broker_min_stop_usd)
    return compute_lot(equity, risk_pct, stop_distance, spec,
                       free_margin=free_margin, price=price, leverage=leverage)


# ---------------------------------------------------------------------------
# Phase C — restart recovery
# ---------------------------------------------------------------------------
# Recovery actions
RECOVER_START_FLAT = "START_FLAT"            # no pre-existing position; begin normally
RECOVER_ADOPT = "ADOPT"                      # rebuild internal state from the live position
RECOVER_MANUAL = "MANUAL_INTERVENTION"       # pre-existing position; human review required -> block
RECOVER_BLOCKED = "BLOCKED"                  # broker/local disagree or query failed -> block


@dataclass
class RecoveryPlan:
    action: str
    positions: list = field(default_factory=list)   # our magic positions found
    orphans_no_sl: list = field(default_factory=list)
    foreign_positions: list = field(default_factory=list)  # other magic/symbol (not ours)
    state_known: bool = True                          # False -> block new entries
    reason: str = ""


def startup_recovery(broker, magic: int, symbol: str, *, policy: str = "manual") -> RecoveryPlan:
    """Read-only reconciliation of broker state on (re)start. NEVER closes or
    modifies a position. ``policy``: 'manual' (default, safest — any
    pre-existing position blocks trading pending human review) or 'adopt'
    (rebuild internal state and continue). Idempotent: a pure read, so repeated
    restarts yield the same plan."""
    try:
        positions = broker.positions()
    except Exception:
        return RecoveryPlan(RECOVER_BLOCKED, state_known=False, reason="positions query failed")
    if positions is None:
        return RecoveryPlan(RECOVER_BLOCKED, state_known=False, reason="positions unavailable")

    mine = [p for p in positions if p.get("magic") == magic and p.get("symbol") == symbol]
    foreign = [p for p in positions if (p.get("magic") != magic or p.get("symbol") != symbol)]
    orphans = [p for p in mine if not p.get("sl")]

    if not mine:
        return RecoveryPlan(RECOVER_START_FLAT, [], [], foreign, True, "no pre-existing scalper position")

    if policy == "adopt":
        # Rebuild internal state; still flag orphans (missing SL) so the caller
        # can fail closed rather than trade an unprotected position.
        known = not orphans
        reason = "adopted existing position" + ("" if known else "; ORPHAN missing SL -> block")
        return RecoveryPlan(RECOVER_ADOPT, mine, orphans, foreign, known, reason)

    return RecoveryPlan(RECOVER_MANUAL, mine, orphans, foreign, False,
                        "pre-existing position requires manual review (policy=manual)")


def adopt_position(params, broker_position: dict, current_bar_index: int, entry_time=None) -> Position:
    """Rebuild internal Position state for an adopted broker position.

    IMPORTANT (Step-4 safety): entry_bar_index is set to the CURRENT bar index,
    so bars_held starts at 0 and grows naturally. Setting it to 0 (as a naive
    adoption would) against a large live bar index makes bars_held appear huge
    and would instantly trip EXIT_MAX_HOLD_TIME — unsafe trade management on an
    inherited position. This prevents that. Peak/worst favorable move reset to 0
    so trailing re-arms from adoption rather than acting on an unknown MFE."""
    side = PositionState.LONG if broker_position.get("side") == "BUY" else PositionState.SHORT
    return Position(
        direction=side,
        entry_price=float(broker_position["entry_price"]),
        entry_time=entry_time,
        entry_bar_index=current_bar_index,
        risk_unit=params.stop_distance_usd,
        ticket=broker_position.get("ticket"),
    )


# ---------------------------------------------------------------------------
# Phase D — fail-closed entry gate
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class EntryGate:
    allowed: bool
    reason: str
    volume: float = 0.0


def entry_gate(*, preflight_ok: bool, broker_state_known: bool, guard_ok: bool,
               sizing: SizingResult | None, duplicate: bool = False) -> EntryGate:
    """Single fail-closed decision for whether an already-signalled entry may be
    executed, and at what size. ANY unknown/invalid condition blocks."""
    if not preflight_ok:
        return EntryGate(False, "BLOCKED_PREFLIGHT")
    if not broker_state_known:
        return EntryGate(False, "BLOCKED_STATE_UNKNOWN")
    if not guard_ok:
        return EntryGate(False, "BLOCKED_RISK_GUARD")
    if duplicate:
        return EntryGate(False, "BLOCKED_DUPLICATE_ORDER")
    if sizing is None or sizing.rejected or sizing.lot <= 0:
        return EntryGate(False, f"BLOCKED_SIZING({sizing.reason if sizing else 'none'})")
    return EntryGate(True, "OK", sizing.lot)


def bar_tag(symbol: str, bar_time) -> str:
    """Stable per-bar client tag used in the order comment so a retry/restart
    on the same bar is detected as a duplicate (reconciliation.would_duplicate)."""
    return f"{symbol}@{int(bar_time.timestamp()) if hasattr(bar_time, 'timestamp') else bar_time}"
