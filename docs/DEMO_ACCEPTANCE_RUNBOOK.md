# XAUUSD Scalper — Demo Acceptance Runbook (procedure only)

**Scope:** manual, DEMO-account acceptance tests to run on the Windows VPS
against a live Exness MT5 **demo** terminal, before any consideration of live
trading. **Nothing here is executed yet.** Each test states whether it needs
separate written authorization.

**Branch:** `fix/p0-p1-reconciliation`. **Account:** Exness **DEMO** only
(the runner hard-aborts if the account is not DEMO). Strategy is LOCKED; these
tests validate infrastructure, not the strategy.

Legend — **AUTH**: requires explicit written authorization before running
(places/affects orders or touches the broker beyond read-only). **READ-ONLY**:
safe, no orders.

---

## Pre-req (one-time, READ-ONLY)
- Windows VPS with native MT5 installed; `SCALPER_MT5_PATH` → `terminal64.exe`.
- `.venv` created, `pip install -r python/requirements.txt`.
- `.env` with Exness **demo** credentials + optional `SCALPER_TELEGRAM_*`
  (never committed).
- Confirm `python -m pytest python/tests -q` is green on the VPS (offline suite).

## A. Symbol specification verification — READ-ONLY
1. Start the runner; capture the `SYMBOL_SPEC` log line.
2. **Pass:** `point`, `tick_size`, `tick_value`, `contract_size`, `volume_min/max/step`
   are present and match the Exness XAUUSDm contract (point `0.001` or `0.01` as
   the broker reports — not assumed). Preflight must NOT abort.
3. **Fail → block:** if `preflight blocked: SPEC_UNVERIFIED`, stop; no sizing is
   safe without the real spec.

## B. Deal-history retrieval — READ-ONLY
1. With the demo account holding past deals, call `broker.history(days=1..7)`.
2. **Pass:** IN/OUT deals return with `position_id`, `profit`, `commission`,
   `swap`. These are the authoritative P&L inputs (Step 2).

## C. Spread & tick-value sanity — READ-ONLY
1. Log `spread_points` at several times; compare `tick_value` to a hand calc
   (`contract_size * tick_size`).
2. **Pass:** spread filter admits/rejects consistently with the live `point`;
   `value_per_lot($1/oz) == 100` for XAU.

## D. Dynamic sizing dry-run — READ-ONLY
1. For demo equity, log the computed lot across a few `stop_distance_usd` values
   (no order sent — inspect `ENTRY_SKIPPED`/planned `lot`).
2. **Pass:** risk ≤ 0.5% budget; min-lot-unsafe cases are skipped; `0.27` never
   produced for a $1,000 equity.

## E. Order reconciliation — **AUTH** (places demo orders)
1. Allow the bot to take a small number of demo trades during a live session.
2. For each close, compare the runner's `realized` (broker-reconciled) to the
   MT5 terminal's deal history and the account **balance delta**.
3. **Pass (acceptance target):** reconciled realized matches broker deal totals
   within **$0.01**; SL-triggered closes are `CONFIRMED` (not lost); no
   `pnl: null`; no double-counting.

## F. Restart / recovery — **AUTH** if a position is open (READ-ONLY if flat)
1. With the bot **flat**: kill the process, restart. **Pass:** `RECOVERY
   START_FLAT`, single instance (lock held), no duplicate entries.
2. With a demo position **open**: kill, restart. **Pass:** `RECOVERY MANUAL`
   (default) blocks new entries pending review; with `policy=adopt`, `ADOPTED`
   with `bars_held` starting at 0 (no spurious `EXIT_MAX_HOLD_TIME`); orphan
   without SL → fail closed. The bot must **not** auto-close/modify the position.
3. Reboot the VPS. **Pass:** exactly one bot instance after boot; no duplicate
   orders.

## G. Telegram delivery — READ-ONLY (sends messages, not orders)
1. With `SCALPER_TELEGRAM_*` set, start/stop the bot.
2. **Pass:** startup, MT5 connected, and shutdown messages arrive. Realized-P&L
   messages are labelled `broker-confirmed`; unknown closes show `UNCONFIRMED`
   (never a fabricated figure).

## H. P&L comparison vs broker history — **AUTH** (follows E)
1. After a demo session, run the (read-only) session reconciliation against
   `history_deals_get` for the magic positions.
2. **Pass:** per-trade and session realized reconcile to the account balance
   delta within rounding; divergence from any strategy-internal figure is
   expected and documented.

## I. Session gating — READ-ONLY
1. Observe behavior across a Friday close / weekend / Sunday open / daily break.
2. **Pass:** `ENTRY_SKIPPED SESSION_CLOSED` outside sessions; monitoring/exits
   still run; entries resume at Sunday open.

---

## Authorization summary
- **READ-ONLY (no separate auth beyond running on demo):** A, B, C, D, G, I,
  and F when the bot is flat.
- **Requires written authorization (affects demo orders/positions):** E, H, and
  F when a demo position is open.

## Exit criteria to consider a (separate, future) live discussion
All READ-ONLY tests pass **and** E/H meet the $0.01 reconciliation target over a
meaningful demo sample **and** no fail-closed/recovery defect is observed. Live
trading remains out of scope and requires its own explicit authorization.
