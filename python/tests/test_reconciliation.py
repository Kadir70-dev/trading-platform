"""Reconciliation + fail-closed decision logic (requirements 5 and 7).

Exercises duplicate-order detection, missing/orphan positions, unknown-state
fail-closed, broker-truth close classification, crash/restart recovery and
reconnect — all offline against SimBroker and pure helpers."""
from mt5.client import SimBroker
from scalper import reconciliation as rec


def _sim():
    return SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0)


# ---- fail-closed gate (requirement 7) ----
def test_trade_gate_fails_closed_when_state_unknown():
    g = rec.trade_gate(broker_state_known=False)
    assert not g.ok and g.reason == "BLOCKED_STATE_UNKNOWN"


def test_trade_gate_allows_when_state_known():
    assert rec.trade_gate(broker_state_known=True).ok


# ---- startup reconciliation / crash recovery (requirement 5) ----
def test_startup_start_flat_when_no_position():
    assert rec.startup_decision([], magic=7, symbol="XAUUSDm")["action"] == "START_FLAT"


def test_startup_flatten_is_default_policy():
    pos = [{"ticket": 1, "magic": 7, "symbol": "XAUUSDm", "sl": 2300.0}]
    assert rec.startup_decision(pos, magic=7, symbol="XAUUSDm")["action"] == "FLATTEN"


def test_startup_adopt_when_requested():
    pos = [{"ticket": 1, "magic": 7, "symbol": "XAUUSDm", "sl": 2300.0}]
    d = rec.startup_decision(pos, magic=7, symbol="XAUUSDm", policy="adopt")
    assert d["action"] == "ADOPT" and d["positions"][0]["ticket"] == 1


def test_startup_ignores_other_magic_or_symbol():
    pos = [{"ticket": 1, "magic": 99, "symbol": "XAUUSDm", "sl": 1.0},
           {"ticket": 2, "magic": 7, "symbol": "EURUSD", "sl": 1.0}]
    assert rec.startup_decision(pos, magic=7, symbol="XAUUSDm")["action"] == "START_FLAT"


def test_crash_recovery_detects_live_position_via_sim():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=2300.0, tp=None, magic=7, comment="bar1")
    # Simulate a process restart: a fresh startup pass reads broker positions.
    d = rec.startup_decision(b.positions(), magic=7, symbol="XAUUSDm", policy="adopt")
    assert d["action"] == "ADOPT" and d["positions"][0]["ticket"] == r.ticket


# ---- duplicate prevention + orphan detection ----
def test_would_duplicate_same_bar_tag():
    pos = [{"ticket": 1, "magic": 7, "symbol": "XAUUSDm", "comment": "scalper@bar1"}]
    assert rec.would_duplicate(pos, 7, "XAUUSDm", "bar1")
    assert not rec.would_duplicate(pos, 7, "XAUUSDm", "bar2")


def test_positions_without_sl_flags_orphans():
    pos = [{"ticket": 1, "magic": 7, "symbol": "XAUUSDm", "sl": 0.0},
           {"ticket": 2, "magic": 7, "symbol": "XAUUSDm", "sl": 2300.0}]
    orphans = rec.positions_without_sl(pos, 7, "XAUUSDm")
    assert [p["ticket"] for p in orphans] == [1]


# ---- close classification against broker history (P0-C) ----
def test_classify_close_open():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.1, sl=2300.0, tp=None, magic=7, comment="t")
    assert rec.classify_close(r.ticket, b.positions(), b.history()) == rec.CLOSE_OPEN


def test_classify_close_by_broker_after_sl():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.1, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b.modify(r.ticket, sl=entry - 3.0, tp=None)
    b.force_sl_close(r.ticket)
    # Not in positions, but an OUT deal exists -> CLOSED_BY_BROKER, not a failure.
    assert rec.classify_close(r.ticket, b.positions(), b.history()) == rec.CLOSE_BY_BROKER


def test_classify_close_truly_absent():
    b = _sim()
    assert rec.classify_close(999999, b.positions(), b.history()) == rec.CLOSE_TRULY_ABSENT


# ---- authoritative realized P&L ----
def test_realized_for_position_includes_costs():
    b = SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0,
                  commission_per_lot=3.5, swap_per_lot_per_close=1.0)
    r = b.market_order("XAUUSDm", "BUY", 1.0, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b._close_at(r.ticket, entry + 1.0)       # +$100 price, -3.5 out commission, -1.0 swap
    out = rec.realized_for_position(r.ticket, b.history())
    assert out["found"] and abs(out["realized"] - (100.0 - 3.5 - 1.0)) < 1e-6


def test_reconnect_is_idempotent():
    b = _sim()
    assert b.connect() and b.connect()       # repeatable, no error
    b.shutdown()
    assert b.connect()
