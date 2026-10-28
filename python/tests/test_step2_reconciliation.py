"""Step 2: authoritative broker-P&L reconciliation (Phase 4 scenarios).

All offline, via SimBroker + the real runner `_execute_close` + the pure
reconciliation helpers. No live/demo orders, no MT5 terminal."""
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from mt5.client import SimBroker
from scalper import reconciliation as rec
from scalper.config import ScalperParams
from scalper.state_machine import ScalperStateMachine

# Import the real runner module (scripts/ is a sibling of python/).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
import run_scalper_demo as runner  # noqa: E402

NOW = datetime(2026, 9, 16, 22, 0, tzinfo=timezone.utc)


def _params(**kw):
    p = ScalperParams(_env_file=None)
    p.symbol = "XAUUSDm"
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def _sm(**kw):
    return ScalperStateMachine(_params(**kw), symbol="XAUUSDm", pnl_mode="broker")


def _sim(**kw):
    return SimBroker(login=1, server="SIM", leverage=2000, balance=1000.0, **kw)


def _open(b, side, vol, entry, sl):
    """Open then pin a deterministic entry/SL (SimBroker entry comes from a
    noisy tick), so realized figures are exact."""
    r = b.market_order("XAUUSDm", side, vol, sl=sl, tp=None, magic=7, comment="bar")
    b._positions[r.ticket]["entry_price"] = entry
    b._positions[r.ticket]["sl"] = sl
    return r.ticket


# --- normal profitable / losing closes --------------------------------------
def test_normal_profitable_close_books_broker_realized():
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3997.0)
    b._positions[t]["entry_price"] = b.tick("XAUUSDm")["bid"] - 2.0   # ensure a profit at market
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert sm.guard.daily_pnl > 0 and sm.guard.broker_state_known


def test_normal_losing_close_books_broker_realized():
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3990.0)
    b._positions[t]["entry_price"] = b.tick("XAUUSDm")["ask"] + 2.0   # ensure a loss at market
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert sm.guard.daily_pnl < 0 and sm.guard.broker_state_known


# --- broker-triggered SL (the live10 case) -----------------------------------
def test_broker_sl_close_is_reconciled_not_lost():
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3997.0)
    b.force_sl_close(t)                                   # broker SL fires first
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    # client close saw 'position not found', but the OUT deal confirms the loss
    assert abs(sm.guard.daily_pnl - (-3.0 * 0.10 * 100)) < 1e-6      # -$30
    assert sm.guard.broker_state_known


# --- partial closure ---------------------------------------------------------
def test_partial_then_full_close_sums_without_double_count():
    b, ledger = _sim(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3990.0)
    b._close_at(t, 4001.0, volume=0.04)                  # +$4 on 0.04
    b._close_at(t, 4002.0, volume=0.06)                  # +$12 on 0.06
    r1 = rec.reconcile_close(t, b.positions(), b.history(), ledger)
    ledger.book_tickets(r1.booked_deal_tickets)
    assert abs(r1.realized - (4.0 + 12.0)) < 1e-6        # both OUT deals, once
    r2 = rec.reconcile_close(t, b.positions(), b.history(), ledger)   # replay
    assert r2.status == rec.STATUS_TRULY_ABSENT and r2.realized is None


# --- commission / swap / entry-side cost accounting --------------------------
def test_commission_and_swap_included_with_entry_side_costs():
    b = _sim(commission_per_lot=3.5, swap_per_lot_per_close=1.0)
    ledger = rec.ReconcileLedger()
    t = _open(b, "BUY", 1.0, entry=4000.0, sl=3990.0)
    b._close_at(t, 4001.0)                               # +$100 price
    r = rec.reconcile_close(t, b.positions(), b.history(), ledger)
    # +100 price  -3.5 entry commission  -3.5 exit commission  -1.0 swap = 92.0
    assert abs(r.realized - 92.0) < 1e-6


# --- missing broker position (truly absent) ----------------------------------
def test_missing_position_without_deal_is_unknown_not_zero():
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3990.0)
    del b._positions[t]                                  # vanished, no OUT deal
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert sm.guard.daily_pnl == 0.0                     # never booked a fake 0
    assert not sm.guard.broker_state_known               # fail closed


# --- history API timeout -----------------------------------------------------
class _FlakyBroker:
    """Wraps SimBroker; history()/positions() raise for the first `fail` calls."""
    def __init__(self, inner, fail_history=0, fail_positions=0):
        self._b, self._fh, self._fp = inner, fail_history, fail_positions
    def __getattr__(self, name):
        return getattr(self._b, name)
    def history(self, days=1):
        if self._fh > 0:
            self._fh -= 1
            raise TimeoutError("history timeout")
        return self._b.history(days=days)
    def positions(self):
        if self._fp > 0:
            self._fp -= 1
            raise TimeoutError("positions timeout")
        return self._b.positions()


def test_history_timeout_recovers_when_query_succeeds():
    inner = _sim()
    t = _open(inner, "BUY", 0.10, entry=4000.0, sl=3997.0)
    inner.force_sl_close(t)
    flaky = _FlakyBroker(inner, fail_history=2)           # first 2 history reads time out
    sm, ledger = _sm(), rec.ReconcileLedger()
    runner._execute_close(flaky, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert abs(sm.guard.daily_pnl - (-30.0)) < 1e-6 and sm.guard.broker_state_known


def test_persistent_timeout_fails_closed():
    inner = _sim()
    t = _open(inner, "BUY", 0.10, entry=4000.0, sl=3997.0)
    inner.force_sl_close(t)
    flaky = _FlakyBroker(inner, fail_history=99)          # always times out
    sm, ledger = _sm(), rec.ReconcileLedger()
    runner._execute_close(flaky, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert sm.guard.daily_pnl == 0.0 and not sm.guard.broker_state_known


# --- duplicate deal replay ---------------------------------------------------
def test_duplicate_reconcile_does_not_double_count():
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3997.0)
    b.force_sl_close(t)
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    first = sm.guard.daily_pnl
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)   # replay same close
    assert sm.guard.daily_pnl == first                   # unchanged


# --- repeated runner restart -------------------------------------------------
def test_restart_with_persisted_ledger_does_not_rebook():
    b = _sim()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3997.0)
    b.force_sl_close(t)
    ledger = rec.ReconcileLedger()
    sm1 = _sm()
    runner._execute_close(b, sm1, ledger, t, "EXIT_LONG", {}, NOW)
    booked = ledger.processed
    # "Restart": brand-new state machine + ledger rehydrated from persisted set.
    sm2, ledger2 = _sm(), rec.ReconcileLedger(processed=booked)
    r = rec.reconcile_close(t, b.positions(), b.history(), ledger2)
    assert r.status == rec.STATUS_TRULY_ABSENT and r.realized is None


# --- daily-loss breaker on broker-reconciled losses --------------------------
def test_daily_loss_breaker_trips_on_broker_realized():
    sm = _sm(max_daily_loss_usd=30.0)
    sm.record_broker_realized(-20.0, NOW)
    sm.record_broker_realized(-15.0, NOW)                # cumulative -35 <= -30
    assert sm.guard.kill_switch
    gr = sm.guard.check_pretrade(spread_points=1.0, now=NOW)
    assert not gr.ok and gr.reason_code == "BLOCKED_KILL_SWITCH"


# --- broker P&L vs strategy-calculated P&L (the core audit finding) -----------
def test_strategy_pnl_never_reaches_guard_in_broker_mode():
    """The live10 trade[1]: strategy computed +297 but the account moved far
    less. In broker mode the strategy figure must NOT touch the guard; only the
    reconciled broker realized does."""
    b, sm, ledger = _sim(), _sm(), rec.ReconcileLedger()
    t = _open(b, "BUY", 0.10, entry=4000.0, sl=3997.0)
    # strategy would think this is a big win; simulate the close path booking
    # the strategy figure into the pending queue (as _close_primary_only does):
    sm._book_result(297.30, NOW, ticket=t, entry_price=4000.0, exit_price=4011.0)
    assert sm.guard.daily_pnl == 0.0                     # strategy pnl NOT in the guard
    assert sm.pending_broker_closes and sm.pending_broker_closes[0]["strategy_pnl"] == 297.30
    # Broker SL actually fired for a small loss; reconcile that instead:
    b.force_sl_close(t)
    runner._execute_close(b, sm, ledger, t, "EXIT_LONG", {}, NOW)
    assert abs(sm.guard.daily_pnl - (-3.0 * 0.10 * 100)) < 1e-6      # -$30, the broker truth
