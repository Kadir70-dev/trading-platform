"""SimBroker contract-math corrections: XAU uses 100 oz/lot, not the FX 100,000.

These lock in requirement 2 (correct P&L and margin from contract specs) and
guard against a regression back to the hardcoded 100,000 multiplier.
"""
from mt5.client import SimBroker, _sim_contract_size


def _sim(**kw):
    return SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0, **kw)


def test_contract_size_xau_is_100_fx_is_100000():
    assert _sim_contract_size("XAUUSD") == 100.0
    assert _sim_contract_size("XAUUSDm") == 100.0
    assert _sim_contract_size("EURUSD") == 100000.0
    assert _sim_contract_size("GBPUSD") == 100000.0


def test_symbol_info_reports_contract_and_consistent_tick_value():
    b = _sim()
    xau = b.symbol_info("XAUUSDm")
    assert xau["contract_size"] == 100.0
    # tick_value must equal contract_size * point (self-consistent), not a flat 1.0 guess
    assert abs(xau["tick_value"] - xau["contract_size"] * xau["point"]) < 1e-12
    eur = b.symbol_info("EURUSD")
    assert eur["contract_size"] == 100000.0


def test_xau_realized_uses_100_not_100000():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    # Close $1.00/oz in profit on 0.10 lot => 1.0 * 0.10 * 100 = $10.00 (not $1000)
    out = b._close_at(r.ticket, entry + 1.0)
    deal = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1][0]
    assert abs(deal["profit"] - 10.0) < 1e-6


def test_fx_realized_unchanged_100000():
    b = _sim()
    r = b.market_order("EURUSD", "BUY", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    # 0.0010 move on 0.10 lot => 0.0010 * 0.10 * 100000 = $10.00
    out = b._close_at(r.ticket, entry + 0.0010)
    deal = [d for d in b.history() if d["position_id"] == r.ticket and d["entry"] == 1][0]
    assert abs(deal["profit"] - 10.0) < 1e-6


def test_margin_uses_contract_size_not_100000():
    b = _sim()
    r = b.market_order("XAUUSDm", "BUY", 0.10, sl=None, tp=None, magic=7, comment="t")
    entry = next(p for p in b.positions() if p["ticket"] == r.ticket)["entry_price"]
    expected = 0.10 * 100.0 * entry / 2000          # XAU contract, not 100000
    buggy = 0.10 * 100000.0 * entry / 2000
    margin = b.account_info()["margin"]
    assert abs(margin - expected) < 0.01
    assert abs(margin - buggy) > 1.0                 # definitely not the old bug
