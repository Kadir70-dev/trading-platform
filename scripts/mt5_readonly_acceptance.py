#!/usr/bin/env python3
"""Step 5A — REAL Exness MT5 DEMO read-only acceptance (NO ORDERS).

Run this on the machine with the MT5 terminal (native Windows, or Wine) using
YOUR demo credentials from .env. It connects read-only and performs ONLY:
account_info / symbol_info / history_deals_get / positions_get. It never
imports or calls market_order or close_position — it is structurally incapable
of placing, modifying or closing an order, and HARD-ABORTS if the account is
not DEMO.

    # native Windows:  .venv\\Scripts\\python.exe scripts\\mt5_readonly_acceptance.py
    # Linux + Wine:    xvfb-run -a wine ~/.mt5/drive_c/Python312/python.exe \\
    #                      "Z:\\home\\<you>\\trading-platform\\scripts\\mt5_readonly_acceptance.py"

It prints a JSON report. No credentials are printed. This script is intended to
be run by the account owner; the assistant does not run it (it will not use your
credentials or open a broker connection on your behalf).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))

from config import get_settings  # noqa: E402
from mt5.client import MT5Broker  # noqa: E402
from mt5.connection import MT5Connection  # noqa: E402
from scalper import execution_safety as es  # noqa: E402
from scalper import reconciliation as rec  # noqa: E402
from scalper.config import ScalperParams  # noqa: E402
from scalper.symbol_spec import SymbolSpec  # noqa: E402

RISK_PCT = 0.005
CAPITALS = (500.0, 1000.0, 4000.0, 10000.0)


def _emit(obj):
    print(json.dumps(obj, default=str))


def main() -> int:
    params = ScalperParams()
    s = get_settings()
    conn = MT5Connection(s.mt5_login, s.mt5_password, s.mt5_server, s.mt5_path)
    if not conn.connect():
        _emit({"step": "connect", "ok": False, "error": "MT5 connect failed"})
        return 1
    broker = MT5Broker(conn)
    try:
        # 1. account + DEMO hard-gate (never touch a REAL account)
        acct = broker.account_info()
        ident = conn.identity()
        demo = ident and ident.mode == "DEMO"
        _emit({"step": "account", "login": acct["login"], "server": acct["server"],
               "mode": ident.mode if ident else "UNKNOWN", "currency": acct["currency"],
               "leverage": acct["leverage"], "balance": acct["balance"],
               "equity": acct["equity"], "is_demo": bool(demo)})
        if not demo:
            _emit({"step": "ABORT", "reason": "account is not DEMO — refusing read-only run"})
            return 2

        # 2. symbol spec + preflight
        pf = es.preflight(broker, params.symbol, ident_mode=ident.mode,
                          trade_allowed=broker.symbol_info(params.symbol).get("trade_allowed"))
        info = broker.symbol_info(params.symbol)
        _emit({"step": "symbol_spec", "preflight_ok": pf.ok, "reason": pf.reason,
               "symbol": params.symbol, "point": info.get("point"), "digits": info.get("digits"),
               "tick_size": info.get("tick_size"), "tick_value": info.get("tick_value"),
               "contract_size": info.get("contract_size"), "volume_min": info.get("volume_min"),
               "volume_max": info.get("volume_max"), "volume_step": info.get("volume_step"),
               "spread_points": info.get("spread_points"), "bid": info.get("bid"), "ask": info.get("ask")})
        if not pf.ok:
            _emit({"step": "ABORT", "reason": f"spec unverifiable: {pf.reason}"})
            return 3
        spec = pf.spec

        # 3. $1,000 (and others) risk-sizing dry run across stop distances (NO orders)
        for cap in CAPITALS:
            rows = []
            for sd in (0.5, 1.0, 2.0, 3.0):
                p = ScalperParams(_env_file=None); p.stop_distance_usd = sd
                r = es.plan_entry_lot(cap, RISK_PCT, spec, p, price=info.get("ask"),
                                      free_margin=acct["free_margin"], leverage=acct["leverage"])
                rows.append({"stop_usd": sd, "lot": r.lot, "risk_usd": r.risk_usd,
                             "budget": r.risk_budget_usd, "rejected": r.rejected, "reason": r.reason})
            _emit({"step": "sizing_dry_run", "capital": cap, "rows": rows})

        # 4. broker deal history reconciliation (read-only; authoritative P&L)
        deals = broker.history(days=30)
        positions = broker.positions()
        by_pos = {}
        for d in deals:
            by_pos.setdefault(d.get("position_id"), []).append(d)
        recon = []
        for pos_id, ds in by_pos.items():
            outs = [x for x in ds if x.get("entry") == 1]
            if not outs:
                continue
            realized = round(sum(float(x.get("profit", 0)) + float(x.get("commission", 0))
                                 + float(x.get("swap", 0)) for x in ds), 2)
            recon.append({"position_id": pos_id, "deals": len(ds), "out_deals": len(outs),
                          "realized_incl_costs": realized,
                          "commission": round(sum(float(x.get("commission", 0)) for x in ds), 2),
                          "swap": round(sum(float(x.get("swap", 0)) for x in ds), 2)})
        _emit({"step": "history_reconcile", "deals_total": len(deals),
               "open_positions": len(positions),
               "reconciled_positions": len(recon),
               "session_realized_sum": round(sum(r["realized_incl_costs"] for r in recon), 2),
               "detail": recon[:50]})

        # 5. live10 cross-check: do the logged tickets appear in broker history?
        live10_tickets = [2387074102, 2387093807, 2387165981, 2387197563, 2387211882,
                          2387237826, 2387271568, 2387305803]  # from scalper_demo_live10.out opens
        found = sorted({d.get("position_id") for d in deals if d.get("position_id") in live10_tickets})
        _emit({"step": "live10_crosscheck", "known_open_tickets_checked": len(live10_tickets),
               "found_in_history": found,
               "note": ("broker deal history older than its retention window will not return "
                        "these; absence here is NOT proof they never happened — do not fabricate.")})

        _emit({"step": "DONE", "ts": datetime.now(timezone.utc).isoformat(),
               "orders_placed": 0, "positions_modified": 0})
        return 0
    finally:
        conn.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
