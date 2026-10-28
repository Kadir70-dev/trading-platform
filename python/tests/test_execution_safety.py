"""Step 3: symbol-spec preflight, dynamic sizing, restart recovery, fail-closed
safety. All offline via SimBroker + pure helpers. No orders placed."""
import pytest

from mt5.client import SimBroker
from scalper import execution_safety as es
from scalper import reconciliation as rec
from scalper.config import ScalperParams
from scalper.symbol_spec import SymbolSpec

MAGIC = 20260916


def _sim(**kw):
    return SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0, **kw)


def _params(**kw):
    p = ScalperParams(_env_file=None)
    p.symbol = "XAUUSDm"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _xau_spec(point=0.01, contract=100.0, step=0.01, vmin=0.01, vmax=50.0):
    return SymbolSpec.from_symbol_info({
        "symbol": "XAUUSDm", "point": point, "digits": 2, "tick_size": point,
        "tick_value": contract * point, "contract_size": contract,
        "volume_min": vmin, "volume_max": vmax, "volume_step": step,
    })


# ========================= Phase A — symbol specs ==========================
def test_preflight_loads_spec_from_broker():
    pf = es.preflight(_sim(), "XAUUSDm", ident_mode="DEMO", trade_allowed=True)
    assert pf.ok and pf.spec.contract_size == 100.0 and pf.spec.point > 0


def test_preflight_blocks_on_mode_mismatch():
    pf = es.preflight(_sim(), "XAUUSDm", ident_mode="REAL", trade_allowed=True)
    assert not pf.ok and "MODE_MISMATCH" in pf.reason


def test_preflight_blocks_when_symbol_not_tradeable():
    pf = es.preflight(_sim(), "XAUUSDm", ident_mode="DEMO", trade_allowed=False)
    assert not pf.ok and pf.reason == "SYMBOL_NOT_TRADEABLE"


def test_preflight_blocks_when_spec_unverifiable():
    class _NoSpec(SimBroker):
        def symbol_info(self, symbol):
            raise ConnectionError("broker down")
    b = _NoSpec(login=1, server="SIM", leverage=2000, balance=1000.0)
    pf = es.preflight(b, "XAUUSDm", ident_mode="DEMO", trade_allowed=True)
    assert not pf.ok and pf.reason == "SPEC_UNVERIFIED"   # no assumed point size


def test_load_spec_rejects_nonpositive_fields():
    class _BadSpec(SimBroker):
        def symbol_info(self, symbol):
            d = super().symbol_info(symbol); d["point"] = 0; return d
    assert es.load_symbol_spec(_BadSpec(login=1, server="S", leverage=2000), "XAUUSDm") is None


# ===================== Phase B — dynamic lot sizing ========================
@pytest.mark.parametrize("capital", [500.0, 1000.0, 4000.0, 10000.0])
def test_sizing_risk_never_exceeds_budget_across_capitals(capital):
    spec, p = _xau_spec(), _params()
    for stop in (0.5, 1.0, 2.0, 3.0, 4.0):
        p.stop_distance_usd = stop
        r = es.plan_entry_lot(capital, 0.005, spec, p)   # emergency = 3R * stop
        if not r.rejected:
            assert r.risk_usd <= r.risk_budget_usd + 1e-9
            assert r.lot >= spec.volume_min
            assert abs(round(r.lot / spec.volume_step) * spec.volume_step - r.lot) < 1e-9


def test_sizing_1000_at_half_percent_default_stop():
    # default stop_distance 1.0 => emergency 3R = $3/oz => $300/lot risk
    # budget $5 => 5/300 = 0.0167 -> step down to 0.01, risking $3 (<= $5)
    r = es.plan_entry_lot(1000.0, 0.005, _xau_spec(), _params(stop_distance_usd=1.0))
    assert r.lot == 0.01 and r.risk_usd == 3.0 and not r.rejected


def test_sizing_skips_trade_when_min_lot_unsafe():
    # $500 account, wide stop: even 0.01 lot risks more than 0.5% ($2.50) -> skip
    r = es.plan_entry_lot(500.0, 0.005, _xau_spec(), _params(stop_distance_usd=2.0))
    assert r.rejected and r.reason == "BELOW_MIN_VOLUME" and r.lot == 0.0


def test_sizing_respects_margin():
    r = es.plan_entry_lot(1000.0, 0.005, _xau_spec(), _params(stop_distance_usd=1.0),
                          price=2350.0, free_margin=1.0, leverage=2000)
    assert r.rejected and r.reason == "INSUFFICIENT_MARGIN"


def test_027_lot_never_produced_for_1000():
    for stop in (0.5, 1.0, 2.0, 3.0):
        r = es.plan_entry_lot(1000.0, 0.005, _xau_spec(), _params(stop_distance_usd=stop))
        assert r.lot < 0.27


def test_broker_min_stop_widens_distance():
    # If broker enforces a wider min stop, risk-per-lot rises -> smaller lot.
    p = _params(stop_distance_usd=1.0)
    narrow = es.plan_entry_lot(10000.0, 0.005, _xau_spec(), p)
    wide = es.plan_entry_lot(10000.0, 0.005, _xau_spec(), p, broker_min_stop_usd=10.0)
    assert wide.lot <= narrow.lot


# ===================== Phase C — restart recovery ==========================
def test_recovery_start_flat_when_no_position():
    plan = es.startup_recovery(_sim(), MAGIC, "XAUUSDm")
    assert plan.action == es.RECOVER_START_FLAT and plan.state_known


def test_recovery_manual_blocks_on_existing_position():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.01, sl=3990.0, tp=None, magic=MAGIC, comment="x")
    plan = es.startup_recovery(b, MAGIC, "XAUUSDm", policy="manual")
    assert plan.action == es.RECOVER_MANUAL and not plan.state_known
    assert plan.positions[0]["ticket"] == r.ticket


def test_recovery_adopt_rebuilds_and_is_known_when_protected():
    b = _sim()
    b.market_order("XAUUSDm", "SELL", 0.01, sl=4010.0, tp=None, magic=MAGIC, comment="x")
    plan = es.startup_recovery(b, MAGIC, "XAUUSDm", policy="adopt")
    assert plan.action == es.RECOVER_ADOPT and plan.state_known and not plan.orphans_no_sl


def test_recovery_adopt_orphan_without_sl_fails_closed():
    b = _sim()
    b.market_order("XAUUSDm", "BUY", 0.01, sl=None, tp=None, magic=MAGIC, comment="x")
    plan = es.startup_recovery(b, MAGIC, "XAUUSDm", policy="adopt")
    assert plan.orphans_no_sl and not plan.state_known   # unprotected -> block


def test_recovery_ignores_foreign_positions():
    b = _sim()
    b.market_order("XAUUSDm", "BUY", 0.01, sl=3990.0, tp=None, magic=999, comment="other")
    plan = es.startup_recovery(b, MAGIC, "XAUUSDm")
    assert plan.action == es.RECOVER_START_FLAT and len(plan.foreign_positions) == 1


def test_recovery_blocks_when_positions_query_fails():
    class _Down(SimBroker):
        def positions(self):
            raise ConnectionError("disconnected")
    plan = es.startup_recovery(_Down(login=1, server="S", leverage=2000), MAGIC, "XAUUSDm")
    assert plan.action == es.RECOVER_BLOCKED and not plan.state_known


def test_recovery_idempotent_across_restarts():
    b = _sim()
    b.market_order("XAUUSDm", "BUY", 0.01, sl=3990.0, tp=None, magic=MAGIC, comment="x")
    a = es.startup_recovery(b, MAGIC, "XAUUSDm", policy="adopt")
    c = es.startup_recovery(b, MAGIC, "XAUUSDm", policy="adopt")
    assert (a.action, a.state_known) == (c.action, c.state_known)
    assert [p["ticket"] for p in a.positions] == [p["ticket"] for p in c.positions]


# ===================== Phase D — fail-closed gate ==========================
def _ok_sizing():
    return es.plan_entry_lot(1000.0, 0.005, _xau_spec(), _params(stop_distance_usd=1.0))


def test_gate_allows_when_everything_ok():
    g = es.entry_gate(preflight_ok=True, broker_state_known=True, guard_ok=True,
                      sizing=_ok_sizing(), duplicate=False)
    assert g.allowed and g.volume > 0


@pytest.mark.parametrize("kwargs,expected", [
    (dict(preflight_ok=False, broker_state_known=True, guard_ok=True), "BLOCKED_PREFLIGHT"),
    (dict(preflight_ok=True, broker_state_known=False, guard_ok=True), "BLOCKED_STATE_UNKNOWN"),
    (dict(preflight_ok=True, broker_state_known=True, guard_ok=False), "BLOCKED_RISK_GUARD"),
])
def test_gate_fails_closed(kwargs, expected):
    g = es.entry_gate(sizing=_ok_sizing(), duplicate=False, **kwargs)
    assert not g.allowed and g.reason == expected


def test_gate_blocks_duplicate_order():
    g = es.entry_gate(preflight_ok=True, broker_state_known=True, guard_ok=True,
                      sizing=_ok_sizing(), duplicate=True)
    assert not g.allowed and g.reason == "BLOCKED_DUPLICATE_ORDER"


def test_gate_blocks_rejected_sizing():
    bad = es.plan_entry_lot(500.0, 0.005, _xau_spec(), _params(stop_distance_usd=2.0))
    g = es.entry_gate(preflight_ok=True, broker_state_known=True, guard_ok=True,
                      sizing=bad, duplicate=False)
    assert not g.allowed and g.reason.startswith("BLOCKED_SIZING")


def test_duplicate_detection_by_bar_tag():
    b = _sim()
    import datetime as dt
    tag = es.bar_tag("XAUUSDm", dt.datetime(2026, 9, 16, 22, 0, tzinfo=dt.timezone.utc))
    b.market_order("XAUUSDm", "BUY", 0.01, sl=3990.0, tp=None, magic=MAGIC, comment=f"scalper|{tag}")
    assert rec.would_duplicate(b.positions(), MAGIC, "XAUUSDm", tag)
    other = es.bar_tag("XAUUSDm", dt.datetime(2026, 9, 16, 22, 1, tzinfo=dt.timezone.utc))
    assert not rec.would_duplicate(b.positions(), MAGIC, "XAUUSDm", other)
