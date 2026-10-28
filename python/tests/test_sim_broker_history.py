"""SimBroker realistic deal history: IN/OUT deals, commission, swap, partial
fills and broker-triggered SL closures (requirement 3)."""
from mt5.client import SimBroker


def _sim(**kw):
    return SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0, **kw)


def test_open_records_in_deal():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=None, tp=None, magic=7, comment="bar1")
    ins = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 0]
    assert len(ins) == 1
    assert ins[0]["volume"] == 0.10 and ins[0]["magic"] == 7 and ins[0]["comment"] == "bar1"


def test_close_records_out_deal():
    b = _sim()
    r = b.market_order("XAUUSDm", "SELL", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b._close_at(r.ticket, entry - 2.0)              # SELL profits when price falls
    outs = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1]
    assert len(outs) == 1
    assert abs(outs[0]["profit"] - (2.0 * 0.10 * 100)) < 1e-6   # +$20


def test_commission_is_charged_both_legs_and_hits_balance():
    b = _sim(commission_per_lot=3.5)
    start = b.account_info()["balance"]
    r = b.market_order("XAUUSDm", "BUY", 1.0, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b._close_at(r.ticket, entry)                    # flat price P&L, only costs move balance
    end = b.account_info()["balance"]
    # round-turn commission = 3.5 (in) + 3.5 (out) on 1.0 lot
    assert abs((start - end) - 7.0) < 1e-6
    deals = [d for d in b.history() if d["position_id"] == r.ticket]
    assert all(d["commission"] <= 0 for d in deals)


def test_swap_is_charged_on_close():
    b = _sim(swap_per_lot_per_close=1.25)
    r = b.market_order("XAUUSDm", "BUY", 2.0, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b._close_at(r.ticket, entry)
    out = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1][0]
    assert abs(out["swap"] - (-1.25 * 2.0)) < 1e-6


def test_partial_close_leaves_residual():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b._close_at(r.ticket, entry + 1.0, volume=0.04)
    residual = [p for p in b.positions() if p["ticket"] == r.ticket]
    assert residual and abs(residual[0]["volume"] - 0.06) < 1e-9
    outs = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1]
    assert len(outs) == 1 and abs(outs[0]["volume"] - 0.04) < 1e-9


def test_force_sl_close_removes_position_but_leaves_deal():
    """The live10 scenario: broker SL fires, the position is gone, but the OUT
    deal exists in history — so a later client close sees 'position not found'."""
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    b.modify(r.ticket, sl=entry - 3.0, tp=None)
    sl_res = b.force_sl_close(r.ticket)
    assert sl_res.ok
    assert all(p["ticket"] != r.ticket for p in b.positions())        # gone
    later = b.close_position(r.ticket)
    assert not later.ok and later.retcode_desc == "position not found"
    outs = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1]
    assert len(outs) == 1 and outs[0]["comment"] == "sl"
    assert abs(outs[0]["profit"] - (-3.0 * 0.10 * 100)) < 1e-6         # -$30
