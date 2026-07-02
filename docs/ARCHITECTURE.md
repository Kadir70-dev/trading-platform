# Architecture

## Component overview

```
                          ┌─────────────────────────────────────────────┐
                          │                  trader (Go)                 │
                          │                                              │
   every 60s ──tick──▶    │  Scheduler ─▶ Risk Engine ─▶ Executor client │
                          │     │             │               │          │
                          │     ▼             ▼               │          │
                          │  Account /     risk_events        │          │
                          │  Positions     audit_logs         │          │
                          │  MarketData      (Store)          │          │
                          │   clients                          │          │
                          └─────┼──────────────────────────────┼─────────┘
              HTTP (JSON)       │                   TCP (line-JSON)
                                │                              │
                  ┌─────────────▼───────────┐     ┌────────────▼──────────────┐
                  │  Python service (8800)  │     │  C++ execution gateway     │
                  │  ─ owns MT5 connection  │◀────│  (8900)                    │
                  │  ─ indicators/strategy  │ HTTP│  ─ trade validation        │
                  │  ─ /account /ohlc       │     │  ─ spread guard            │
                  │  ─ /signal /order/*     │     │  ─ latency monitor         │
                  │                         │     │  ─ order router (FIX seam) │
                  └─────────────┬───────────┘     └────────────────────────────┘
                                │
              ┌─────────────────▼─────────────────┐        ┌──────────────────┐
              │  MetaTrader5 (Wine) ── Exness      │        │  PostgreSQL       │
              │  OR deterministic simulation       │        │  (or file store)  │
              └────────────────────────────────────┘        └──────────────────┘
```

## Why this split

- **Python owns MT5.** The `MetaTrader5` package binds one terminal per process and
  is Windows-only (Wine on Linux). Concentrating it in one service keeps the Wine
  boundary in exactly one place and lets Go/C++ stay native and fast.
- **C++ is in the execution path, not bypassed.** Orders flow Go → C++ → broker.
  The gateway does final validation, live-spread rejection, and latency
  measurement. This is the component that later becomes a FIX engine.
- **Go is the brain.** Deterministic control flow (scheduling, risk, position
  management, persistence, DI wiring) where correctness and readability matter most.

## Trading workflow (one tick)

1. `GET /account` → snapshot balance/equity/margin; persist; feed daily-loss tracker.
2. `GET /positions` → manage open trades: move SL to breakeven at +1R, then trail by 1R.
3. Daily-loss breaker check → halt for the day if tripped.
4. For each symbol:
   a. `POST /signal` → BUY/SELL/HOLD + confidence + ATR stops (persisted).
   b. `GET /symbol/{s}` → digits, point, spread, volume constraints.
   c. **Risk engine** evaluates & sizes (or blocks with a coded reason).
   d. Approved → `OrderRequest` with idempotent `client_id` → **C++ gateway**.
   e. Gateway validates, checks spread, routes, measures latency → `OrderResult`.
   f. Persist execution + audit log.

## Interfaces & dependency injection

All seams are Go interfaces in [`internal/interfaces`](../internal/interfaces/interfaces.go):
`MarketData`, `AccountService`, `PositionService`, `RiskEngine`, `Executor`, `Store`.
`cmd/trader/main.go` constructs concrete implementations and injects them, so any
piece is swappable in tests or production (e.g. `Executor` = C++ gateway today,
`DirectExecutor` as fallback, FIX engine tomorrow).

## Protocols

- **Go ↔ Python:** HTTP/JSON (`internal/httpx`).
- **Go ↔ C++:** line-delimited JSON over TCP (`{"cmd":"execute","order":{…}}` →
  `OrderResult`). Trivial to swap for gRPC/shared-memory later.
- **C++ ↔ Python:** HTTP/JSON (`cpp/include/http_client.hpp`).

## Future HFT / multi-broker roadmap

The `Executor` interface and `OrderRouter::route` seam are the migration points:

| Target | Change |
|--------|--------|
| **FIX API** | Replace `OrderRouter::route` HTTP forward with a FIX session (QuickFIX/C++). Keep validation/monitoring. |
| **Interactive Brokers / Binance Futures** | New `Executor` implementations behind the same Go interface. |
| **Multi-broker / multi-account** | Scheduler fans out per (broker, account); router keyed by venue. |
| **Order-book / low-latency** | Swap TCP JSON for shared memory; pin gateway threads; pre-allocate. |

Because risk, scheduling, and strategy never import a broker SDK directly, adding a
venue touches only the executor/router layer.
