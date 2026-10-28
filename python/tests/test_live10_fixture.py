"""Deterministic regression fixture for the historical 15-trade live10 session
(requirement 4). Proves, from BROKER-SOURCED balances only, that the session
was a net LOSS and that the strategy's internal P&L diverges from the account —
without assuming the -$199.55 is strategy P&L and without fabricating fills."""
import json
from pathlib import Path

from scalper import reconciliation as rec

FIXTURE = Path(__file__).parent / "fixtures" / "live10_session.json"


def _load():
    return json.loads(FIXTURE.read_text())


def test_fixture_shape_matches_log():
    d = _load()
    assert d["symbol"] == "XAUUSDm" and d["mode"] == "DEMO" and d["login"] == 474586531
    assert d["num_opens"] == 15 and d["num_closes"] == 15
    assert len(d["trades"]) == 15


def test_seven_closes_have_no_broker_pnl_and_are_not_fabricated():
    d = _load()
    missing = [t for t in d["trades"] if not t["has_broker_pnl_field"]]
    assert len(missing) == 7
    # NOT fabricated: the broker pnl field stays null for those.
    assert all(t["broker_pnl_field"] is None for t in missing)


def test_session_was_a_net_loss_from_broker_balance():
    d = _load()
    assert d["start_balance"] == 4537.16 and d["end_balance"] == 4337.61
    assert abs(d["session_realized_from_balance"] - (-199.55)) < 0.01   # a LOSS, not +$250


def test_strategy_pnl_diverges_from_broker_truth():
    d = _load()
    # The strategy believed it was slightly up; the account was ~$200 down.
    assert abs(d["strategy_pnl_sum"] - 17.71) < 0.01
    assert d["strategy_pnl_sum"] > 0 > d["session_realized_from_balance"]
    assert abs(d["strategy_pnl_sum"] - d["session_realized_from_balance"]) > 200.0


def test_balance_deltas_reconcile_to_session_total():
    d = _load()
    close_balances = [t["balance_after"] for t in d["trades"]]
    per_trade = rec.realized_from_balance_sequence(close_balances, d["start_balance"])
    assert abs(sum(per_trade) - d["session_realized_from_balance"]) < 0.01
    # And the helper's per-trade figures match the fixture's own derivation.
    assert per_trade == [t["realized_from_balance"] for t in d["trades"]]
