"""Step 5B: offline tests for real MT5 field mapping and restart M1 clock.

No broker sessions, order calls, credentials or external network.
"""
from types import SimpleNamespace as NS
from datetime import datetime, timezone

import pytest

from mt5 import client as bc
from scalper import execution_safety as es
from scalper import recovery_clock as rc
from scalper.config import ScalperParams

T0 = 1_800_000_000  # UTC minute boundary
MAGIC = 20260916


def _pos(**overrides):
    p = {"ticket": 55, "identifier": 55, "magic": MAGIC,
         "symbol": "XAUUSDm", "type": 0, "volume": 0.01,
         "price_open": 4000.0, "sl": 3997.0, "tp": 0.0,
         "swap": 0.0, "profit": 1.0, "comment": "scalper|bar",
         "time": T0, "time_msc": T0 * 1000 + 2000}
    p.update(overrides)
    return NS(**p)


def _deal(**overrides):
    d = {"ticket": 901, "order": 800, "position_id": 55,
         "symbol": "XAUUSDm", "type": 0, "entry": 0, "volume": 0.01,
         "price": 4000.0, "profit": 0.0, "commission": -0.01,
         "swap": 0.0, "fee": 0.0, "magic": MAGIC,
         "comment": "scalper", "time": T0,
         "time_msc": T0 * 1000 + 2000}
    d.update(overrides)
    return NS(**d)


def _history_deal(**overrides):
    d = {"position_id": 55, "order": 800, "entry": 0,
         "time_msc": T0 * 1000 + 2000}
    d.update(overrides)
    return d


def _broker_pos(**overrides):
    p = {"ticket": 55, "position_id": 55, "side": "BUY",
         "entry_price": 4000.0, "sl": 3997.0,
         "time_msc": T0 * 1000 + 2000}
    p.update(overrides)
    return p


def _adopt(bars=10, **overrides):
    p = ScalperParams(_env_file=None)
    kw = dict(history_deals=[_history_deal()],
              m1_bar_opens_sec=[T0 + 60 * i for i in range(bars + 1)],
              current_bar_open_sec=T0 + 60 * bars)
    kw.update(overrides)
    return es.adopt_position(p, _broker_pos(), 298, **kw)


def test_mt5_positions_expose_magic_identity_and_original_open_time(monkeypatch):
    fake = NS(POSITION_TYPE_BUY=0, positions_get=lambda: [_pos()])
    monkeypatch.setattr(bc, "mt5", fake)
    ps = bc.MT5Broker(None).positions()
    assert ps[0]["magic"] == MAGIC
    assert ps[0]["position_id"] == 55
    assert ps[0]["time_msc"] == T0 * 1000 + 2000
    assert ps[0]["comment"] == "scalper|bar"


def test_mt5_position_open_time_not_update_time(monkeypatch):
    fake = NS(POSITION_TYPE_BUY=0,
              positions_get=lambda: [_pos(time_update=T0 + 500, time_update_msc=(T0 + 500) * 1000)])
    monkeypatch.setattr(bc, "mt5", fake)
    assert bc.MT5Broker(None).positions()[0]["time_msc"] == T0 * 1000 + 2000


def test_mt5_positions_query_failure_is_not_flat(monkeypatch):
    monkeypatch.setattr(bc, "mt5", NS(positions_get=lambda: None))
    with pytest.raises(RuntimeError, match="positions_get"):
        bc.MT5Broker(None).positions()


def test_mt5_empty_positions_is_known_flat(monkeypatch):
    monkeypatch.setattr(bc, "mt5", NS(positions_get=lambda: []))
    assert bc.MT5Broker(None).positions() == []


def test_mt5_history_exposes_reconciliation_fields(monkeypatch):
    monkeypatch.setattr(bc, "mt5", NS(history_deals_get=lambda *_: [_deal()]))
    ds = bc.MT5Broker(None).history()
    assert ds[0]["position_id"] == 55
    assert ds[0]["entry"] == 0
    assert ds[0]["magic"] == MAGIC
    assert ds[0]["time_msc"] == T0 * 1000 + 2000
    assert ds[0]["commission"] == -0.01


def test_mt5_history_failure_is_not_empty_history(monkeypatch):
    monkeypatch.setattr(bc, "mt5", NS(history_deals_get=lambda *_: None))
    with pytest.raises(RuntimeError, match="history_deals_get"):
        bc.MT5Broker(None).history()


def test_m1_history_uses_read_only_utc_range(monkeypatch):
    observed = []
    def read_only_range(symbol, timeframe, from_dt, to_dt):
        observed.append((symbol, timeframe, from_dt, to_dt))
        return [{"time": T0}, {"time": T0 + 60}]
    monkeypatch.setattr(bc, "mt5", NS(TIMEFRAME_M1=1, copy_rates_range=read_only_range))
    assert bc.MT5Broker(None).m1_bar_opens("XAUUSDm", T0, T0 + 60) == [T0, T0 + 60]
    assert observed[0][2].tzinfo == timezone.utc


def test_m1_history_query_failure_blocks(monkeypatch):
    monkeypatch.setattr(bc, "mt5", NS(TIMEFRAME_M1=1, copy_rates_range=lambda *_: None))
    with pytest.raises(RuntimeError, match="M1 history"):
        bc.MT5Broker(None).m1_bar_opens("XAUUSDm", T0, T0 + 60)


@pytest.mark.parametrize("n", [0, 1, 10, 100, 239])
def test_adoption_preserves_observed_m1_age(n):
    pos = _adopt(n)
    assert 298 - pos.entry_bar_index == n
    assert pos.entry_time == datetime.fromtimestamp((T0 * 1000 + 2000)/1000, timezone.utc)


@pytest.mark.parametrize("n", [240, 241, 300])
def test_adoption_past_max_hold_requires_manual_review(n):
    with pytest.raises(ValueError, match="MAX_HOLD_REACHED"):
        _adopt(n)


def test_adoption_blocks_unknown_open_time():
    with pytest.raises(ValueError, match="OPEN_TIMESTAMP_NOT_ESTABLISHED"):
        es.adopt_position(ScalperParams(_env_file=None),
                          _broker_pos(time_msc=None), 298,
                          history_deals=[], m1_bar_opens_sec=[T0],
                          current_bar_open_sec=T0)


def test_adoption_blocks_missing_broker_history():
    with pytest.raises(ValueError, match="history unavailable"):
        _adopt(history_deals=None)


def test_adoption_blocks_conflicting_entry_deal():
    with pytest.raises(ValueError, match="TIME_CONFLICT"):
        _adopt(history_deals=[_history_deal(time_msc=(T0 + 1000) * 1000)])


def test_adoption_partial_out_preserves_original_age():
    pos = _adopt(history_deals=[
        _history_deal(),
        _history_deal(entry=1, order=801, time_msc=(T0 + 60) * 1000)])
    assert 298 - pos.entry_bar_index == 10


def test_adoption_multiple_entry_orders_blocked():
    with pytest.raises(ValueError, match="MULTIPLE_ENTRY_ORDERS"):
        _adopt(history_deals=[_history_deal(),
             _history_deal(entry=0, order=801, time_msc=(T0 + 60) * 1000)])


def test_adoption_netting_reversal_blocked():
    with pytest.raises(ValueError, match="NETTING_REVERSAL"):
        _adopt(history_deals=[_history_deal(entry=2)])


def test_adoption_missing_sl_blocked():
    p = _broker_pos(sl=0.0)
    with pytest.raises(ValueError, match="no protective SL"):
        es.adopt_position(ScalperParams(_env_file=None), p, 298,
                          history_deals=[], m1_bar_opens_sec=[T0],
                          current_bar_open_sec=T0)


def test_weekend_gap_counts_only_observed_candles():
    # Intentionally sparse Friday->Monday sample; no phantom closed M1 bars.
    bars = [T0, T0 + 60, T0 + 60 + 3 * 86400]
    pos = _adopt(m1_bar_opens_sec=bars, current_bar_open_sec=bars[-1])
    assert 298 - pos.entry_bar_index == 2


def test_missing_start_candle_blocks_adoption():
    with pytest.raises(ValueError, match="M1_HISTORY_DOES_NOT_SPAN"):
        _adopt(m1_bar_opens_sec=[T0 + 60, T0 + 120])


def test_rolling_window_advance_not_reset():
    pos = NS(entry_bar_index=288)
    times = list(range(100, 400))
    assert es.advance_position_clock(pos, 398, times) == 1
    assert pos.entry_bar_index == 287
    assert 299 - pos.entry_bar_index == 12
    assert es.advance_position_clock(pos, 399, list(range(101, 401))) == 1
    assert 299 - pos.entry_bar_index == 13


def test_rolling_clock_counts_new_closed_bars():
    pos = NS(entry_bar_index=288)
    assert es.advance_position_clock(pos, 397, list(range(100, 400))) == 2
    assert pos.entry_bar_index == 286


def test_rolling_window_loses_previous_bar_fails_closed():
    with pytest.raises(ValueError, match="continuity"):
        es.advance_position_clock(NS(entry_bar_index=288), 50, list(range(100, 400)))


def test_multiple_positions_cannot_auto_adopt():
    class B:
        def positions(self):
            return [_broker_pos(ticket=55, position_id=55, magic=MAGIC, symbol="XAUUSDm"),
                    _broker_pos(ticket=56, position_id=56, magic=MAGIC, symbol="XAUUSDm")]
    plan = es.startup_recovery(B(), MAGIC, "XAUUSDm", policy="adopt")
    assert not plan.state_known and plan.action == es.RECOVER_MANUAL


def test_clock_duplicate_restart_is_deterministic():
    assert _adopt().entry_bar_index == _adopt().entry_bar_index
