#!/usr/bin/env python3
"""Deterministic fixture builder for the historical `live10` demo session.

Reads the REAL demo log (``logs/scalper_demo_live10.out``) and emits
``live10_session.json``, pairing each ORDER_OPEN with its ORDER_CLOSE and
recording, per trade:

* the broker ``pnl`` field as logged (``null`` for SL-closed trades the runner
  could not reconcile — left ``null``, never fabricated),
* ``realized_from_balance`` = the delta of the broker-reported account
  ``balance`` across that close (broker-sourced truth, not invented),
* the strategy's own ``close_detail.pnl`` (for divergence comparison only).

Run (read-only):
    python tests/fixtures/build_live10_fixture.py \
        ../logs/scalper_demo_live10.out tests/fixtures/live10_session.json

This script fabricates nothing: every number is copied or differenced from the
log. Missing broker fills stay missing (``broker_pnl_field: null``).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def build(log_path: Path) -> dict:
    opens: list[dict] = []
    closes: list[dict] = []
    login = server = mode = symbol = None
    start_balance = None

    for line in log_path.read_text().splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        ev = d.get("event")
        if ev == "PRE_FLIGHT_CHECK":
            login, server, mode, symbol = d.get("login"), d.get("server"), d.get("mode"), d.get("symbol")
            start_balance = d.get("balance")
        elif ev == "ORDER_OPEN":
            opens.append(d)
        elif ev == "ORDER_CLOSE":
            closes.append(d)

    trades = []
    prev_balance = start_balance
    for i, c in enumerate(closes):
        o = opens[i] if i < len(opens) else {}
        bal_after = c.get("balance")
        realized_from_balance = (round(bal_after - prev_balance, 2)
                                 if (bal_after is not None and prev_balance is not None) else None)
        cd = c.get("close_detail") or {}
        trades.append({
            "seq": i + 1,
            "ticket": o.get("ticket"),
            "side": o.get("side"),
            "lot": o.get("lot"),
            "entry": o.get("entry"),
            "sl": o.get("sl"),
            "open_balance": o.get("balance"),
            "close_reason": c.get("close_reason"),
            "close_price_field": c.get("close_price"),       # null on SL-closed
            "broker_pnl_field": c.get("pnl"),                # null on SL-closed — NOT fabricated
            "has_broker_pnl_field": c.get("pnl") is not None,
            "retcode_desc": c.get("retcode_desc"),
            "balance_after": bal_after,
            "realized_from_balance": realized_from_balance,  # broker-sourced per-trade truth
            "strategy_pnl": cd.get("pnl"),                   # strategy's own number (for divergence)
        })
        if bal_after is not None:
            prev_balance = bal_after

    end_balance = prev_balance
    return {
        "source": str(log_path.name),
        "login": login, "server": server, "mode": mode, "symbol": symbol,
        "start_balance": start_balance,
        "end_balance": end_balance,
        "session_realized_from_balance": (round(end_balance - start_balance, 2)
                                          if (end_balance is not None and start_balance is not None) else None),
        "num_opens": len(opens),
        "num_closes": len(closes),
        "num_without_broker_pnl": sum(1 for t in trades if not t["has_broker_pnl_field"]),
        "strategy_pnl_sum": round(sum(t["strategy_pnl"] for t in trades if t["strategy_pnl"] is not None), 2),
        "trades": trades,
    }


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__)
        return 2
    log_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    fixture = build(log_path)
    out_path.write_text(json.dumps(fixture, indent=2))
    print(f"wrote {out_path} : {fixture['num_closes']} trades, "
          f"session_realized_from_balance={fixture['session_realized_from_balance']}, "
          f"without_broker_pnl={fixture['num_without_broker_pnl']}, "
          f"strategy_pnl_sum={fixture['strategy_pnl_sum']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
