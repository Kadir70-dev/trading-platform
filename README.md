# Exness MT5 Automated Trading Platform

A production-grade, polyglot automated trading platform for **Exness MetaTrader 5**.
Trend-following **EMA20 / EMA50 / RSI14** strategy with enterprise risk management,
designed for Demo and Real accounts and Exness leverage up to **1:2000**.

```
Go (orchestration)  →  C++ (execution gateway)  →  Python (MT5 + strategy)
   scheduler/risk         validation/latency           broker + indicators
```

- **Go 1.22+** — scheduler, risk engine, CLI, health/metrics, persistence
- **C++20** — low-latency execution gateway: validation, spread & latency monitoring, order routing (FIX-ready seam)
- **Python 3.12+** — MT5 connectivity, indicators, strategy, signals, backtesting

> **Linux + MT5 reality:** the official `MetaTrader5` Python package is Windows-only.
> On Ubuntu it runs via a **Wine bridge** (see [docs/MT5_LINUX_WINE.md](docs/MT5_LINUX_WINE.md)).
> Until that's configured — or inside Docker — the Python service transparently
> falls back to a deterministic **simulation backend** so the entire pipeline runs
> and is testable end-to-end. `GET /health` reports the active backend.

---

## Quick start (Ubuntu 24.04)

```bash
# 1. Build everything (Go + C++ + Python venv)
./scripts/install_ubuntu.sh

# 2. Configure
cp .env.example .env && $EDITOR .env      # set MT5_LOGIN / MT5_PASSWORD / MT5_SERVER

# 3. Run the full stack locally (Python + C++ gateway + Go scheduler)
./scripts/run_local.sh
```

Or with Docker (Postgres + all services, simulation backend):

```bash
docker compose up --build
```

---

## CLI

```bash
trader connect            # verify connectivity, print broker/server/leverage/balance
trader account            # account dashboard (balance, equity, margin, margin level)
trader positions          # list open positions
trader strategy XAUUSD    # print the current signal (BUY/SELL/HOLD + confidence)
trader buy  XAUUSD -lot 0.05 [-sl P -tp P]   # manual order (stops derived from ATR if omitted)
trader sell EURUSD -lot 0.01
trader close 100001 [-vol 0.02]              # close / partial close
trader close-all
trader backtest -sym XAUUSD -tf M5 -bars 5000
trader run                # start the every-minute trading loop + HTTP server
```

Example:

```
$ trader connect
==================== CONNECTION ====================
Broker:    Exness
Server:    Exness-MT5Trial15
Account:   123456789 (DEMO)
Leverage:  1:2000
Balance:   100.00 USD
Equity:    100.00 USD
===================================================
```

---

## Strategy

| Signal | Conditions |
|--------|-----------|
| **BUY**  | `EMA20 > EMA50` **and** `RSI14 > 50` **and** `price > EMA20` |
| **SELL** | `EMA20 < EMA50` **and** `RSI14 < 50` **and** `price < EMA20` |
| **HOLD** | otherwise, or confidence below the floor |

- Confidence (0–100) blends EMA separation, RSI distance from neutral, and price
  displacement, each normalised by ATR. Weak signals are downgraded to HOLD.
- Stops are ATR-based: `SL = SL_ATR_MULT × ATR`, `TP = TP_RR × SL`.
- `XAUUSD` uses a tuned `GoldStrategy` (wider stops, higher confidence floor).
- Timeframes: M1 (default), M5, M15, M30, H1. Symbols are config-driven.

## Risk management (enforced in Go, pre-trade)

Position sizing from `MAX_RISK_PCT` of equity and the SL distance, plus hard blocks for:
spread too high, missing SL/TP, reward:risk below `MIN_RR`, duplicate same-direction
position, max open trades, max per-symbol exposure, insufficient margin, daily-loss
breaker, drawdown breaker, and a global emergency stop. Every block is logged as a
`risk_event`. See [`services/risk/risk.go`](services/risk/risk.go).

## Safety engine — never trades when

spread too high · market/symbol disabled · margin insufficient · MT5 disconnected ·
duplicate position exists · leverage/mode mismatch (warned) · emergency stop engaged.

---

## Architecture

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for diagrams and the FIX/HFT
migration path. In short:

- The **Python service** is the single owner of the MT5 connection (one Wine
  boundary, one terminal-per-process). It exposes account/market/order endpoints.
- The **C++ gateway** sits in the execution path: validates orders, checks live
  spread, measures latency, routes to the broker. `OrderRouter::route` is the
  exact seam where a FIX session replaces the HTTP forward.
- The **Go scheduler** is the brain: every minute it pulls account state, manages
  open positions (breakeven + trailing), then per symbol generates a signal,
  applies risk, and dispatches approved orders to the gateway.

## Persistence

Default: dependency-free **file store** (JSONL under `logs/`) so the platform runs
without a database. Production: build with `-tags postgres` and set `DATABASE_URL`
to use **PostgreSQL** (`scripts/schema.sql`: accounts, signals, orders, executions,
positions, risk_events, audit_logs).

## Monitoring

Go HTTP server (`GO_HTTP_ADDR`, default `:8080`):
`/health`, `/account`, `/positions`, `/metrics` (Prometheus text:
`trader_balance`, `trader_equity`, `trader_margin_level`, `trader_open_positions`).
The C++ gateway answers `{"cmd":"ping"}` with routed/filled/rejected counts and
p50/p99 latency.

## Testing

```bash
make test          # Go + Python
go test ./...      # 10 risk-engine tests
cd python && pytest tests/ -q   # 12 indicator + strategy tests
```

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — components, data flow, HFT roadmap
- [docs/MT5_LINUX_WINE.md](docs/MT5_LINUX_WINE.md) — running real MT5 on Ubuntu via Wine
- [docs/EXNESS_SETUP.md](docs/EXNESS_SETUP.md) — account, 1:2000 leverage, Demo→Real
- [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) — Docker & production deployment
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — common issues

## Repository layout

```
cmd/trader        Go CLI + daemon entrypoint
internal/         config, models, interfaces, logging, repositories (store)
services/         scheduler, risk, marketdata, account, positions, execution
cpp/              execution_engine + order_router/spread/latency/position monitors
python/           mt5/ indicators/ strategies/ backtesting/ analytics/ + service.py
config/           config.yaml (static), .env (secrets)
scripts/          install, wine bridge, run, schema.sql
docker/           per-service Dockerfiles
docs/             guides
```

## ⚠️ Disclaimer

Trading leveraged instruments carries substantial risk of loss. This software is
provided as-is for educational and engineering purposes. **Always validate on a
Demo account first.** You are responsible for any real-money use.
