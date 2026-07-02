# Deployment

## Option A — Docker Compose (recommended for dev / sim)

```bash
cp .env.example .env
docker compose up --build
```

Brings up Postgres (schema auto-applied), the Python service (simulation backend
in-container), the C++ gateway, and the Go scheduler with the PostgreSQL store.

Endpoints: Go `:8080` (`/health`, `/metrics`), Python `:8800`, gateway `:8900`,
Postgres `:5432`.

> Containers use the **simulation** backend. For live trading, run the Python
> service on a Wine-MT5 host (see [MT5_LINUX_WINE.md](MT5_LINUX_WINE.md)) and set
> `PYTHON_SERVICE_ADDR` for the `cpp-gateway` and `trader` services to that host.

## Option B — Native (recommended for live)

```bash
./scripts/install_ubuntu.sh        # build Go + C++, create venv
./scripts/setup_wine_mt5.sh        # set up real MT5 (live only)
# edit .env
./scripts/run_local.sh             # Python + gateway + scheduler
```

### systemd units (production)

Create three units so the stack survives reboots and restarts on failure:

```ini
# /etc/systemd/system/trader-python.service
[Unit]
Description=MT5 Python service
After=network.target
[Service]
WorkingDirectory=/opt/trading-platform/python
# live: use the Wine python; sim: use ../.venv/bin/uvicorn
ExecStart=/usr/bin/xvfb-run -a env WINEPREFIX=/root/.mt5 wine python service.py
Restart=always
RestartSec=5
[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/trader-gateway.service
[Unit]
Description=C++ execution gateway
After=trader-python.service
[Service]
EnvironmentFile=/opt/trading-platform/.env
ExecStart=/opt/trading-platform/cpp/build/execution_gateway
Restart=always
[Install]
WantedBy=multi-user.target
```

```ini
# /etc/systemd/system/trader.service
[Unit]
Description=Go trading scheduler
After=trader-gateway.service postgresql.service
[Service]
WorkingDirectory=/opt/trading-platform
EnvironmentFile=/opt/trading-platform/.env
ExecStart=/opt/trading-platform/bin/trader run
Restart=always
[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now trader-python trader-gateway trader
```

## PostgreSQL store

```bash
# build the Go binary with DB support
go get github.com/jackc/pgx/v5
go build -tags postgres -o bin/trader ./cmd/trader
# apply schema
psql "$DATABASE_URL" -f scripts/schema.sql      # or: make db-init
```

Without `-tags postgres` (or with an empty `DATABASE_URL`) the platform uses the
JSONL file store under `logs/` — fine for dev, not for production audit trails.

## Production checklist

- [ ] `ACCOUNT_MODE` matches the intended account; verified by `trader connect`.
- [ ] `LEVERAGE` matches broker; leverage logged at startup.
- [ ] `MAX_RISK_PCT`, `MAX_DAILY_LOSS_PCT`, `MAX_DRAWDOWN_PCT`, `MAX_OPEN_TRADES` set.
- [ ] `EMERGENCY_STOP=false` (flip to `true` to halt + block new trades).
- [ ] Postgres reachable; schema applied; `-tags postgres` build deployed.
- [ ] `/health` and `/metrics` scraped (Prometheus); alert on `MT5_DISCONNECTED`.
- [ ] `.env` permissions `600`; never committed.
- [ ] Validated on Demo for a full session before Real.
```
