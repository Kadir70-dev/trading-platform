-- ==========================================================
-- Trading Platform schema (PostgreSQL 15+)
-- Idempotent: safe to re-run.
-- ==========================================================

CREATE TABLE IF NOT EXISTS accounts (
    id              BIGSERIAL PRIMARY KEY,
    login           BIGINT       NOT NULL,
    broker          TEXT         NOT NULL,
    server          TEXT         NOT NULL,
    mode            TEXT         NOT NULL CHECK (mode IN ('DEMO','REAL')),
    currency        TEXT         NOT NULL DEFAULT 'USD',
    leverage        INT          NOT NULL,
    balance         NUMERIC(20,5) NOT NULL DEFAULT 0,
    equity          NUMERIC(20,5) NOT NULL DEFAULT 0,
    margin          NUMERIC(20,5) NOT NULL DEFAULT 0,
    free_margin     NUMERIC(20,5) NOT NULL DEFAULT 0,
    margin_level    NUMERIC(20,5) NOT NULL DEFAULT 0,
    snapshot_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (login, snapshot_at)
);
CREATE INDEX IF NOT EXISTS idx_accounts_login ON accounts(login, snapshot_at DESC);

CREATE TABLE IF NOT EXISTS signals (
    id              BIGSERIAL PRIMARY KEY,
    symbol          TEXT         NOT NULL,
    timeframe       TEXT         NOT NULL,
    side            TEXT         NOT NULL CHECK (side IN ('BUY','SELL','HOLD')),
    confidence      NUMERIC(5,2) NOT NULL,
    price           NUMERIC(20,5),
    ema_fast        NUMERIC(20,5),
    ema_slow        NUMERIC(20,5),
    rsi             NUMERIC(20,5),
    atr             NUMERIC(20,5),
    reason          TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_signals_symbol_time ON signals(symbol, created_at DESC);

CREATE TABLE IF NOT EXISTS orders (
    id              BIGSERIAL PRIMARY KEY,
    client_id       TEXT         NOT NULL UNIQUE,        -- idempotency key
    symbol          TEXT         NOT NULL,
    side            TEXT         NOT NULL CHECK (side IN ('BUY','SELL')),
    type            TEXT         NOT NULL DEFAULT 'MARKET',
    volume          NUMERIC(20,5) NOT NULL,
    requested_price NUMERIC(20,5),
    sl              NUMERIC(20,5),
    tp              NUMERIC(20,5),
    status          TEXT         NOT NULL DEFAULT 'PENDING'
                       CHECK (status IN ('PENDING','SENT','FILLED','REJECTED','CANCELLED','ERROR')),
    signal_id       BIGINT       REFERENCES signals(id),
    magic           BIGINT,
    comment         TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_at DESC);

CREATE TABLE IF NOT EXISTS executions (
    id              BIGSERIAL PRIMARY KEY,
    order_id        BIGINT       REFERENCES orders(id),
    broker_ticket   BIGINT,
    deal_ticket     BIGINT,
    symbol          TEXT         NOT NULL,
    side            TEXT         NOT NULL,
    volume          NUMERIC(20,5) NOT NULL,
    price           NUMERIC(20,5) NOT NULL,
    commission      NUMERIC(20,5) DEFAULT 0,
    swap            NUMERIC(20,5) DEFAULT 0,
    spread_points   NUMERIC(20,5),
    latency_ms      NUMERIC(20,5),
    retcode         INT,
    retcode_desc    TEXT,
    executed_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_exec_ticket ON executions(broker_ticket);

CREATE TABLE IF NOT EXISTS positions (
    id              BIGSERIAL PRIMARY KEY,
    ticket          BIGINT       NOT NULL,
    symbol          TEXT         NOT NULL,
    side            TEXT         NOT NULL CHECK (side IN ('BUY','SELL')),
    volume          NUMERIC(20,5) NOT NULL,
    entry_price     NUMERIC(20,5) NOT NULL,
    sl              NUMERIC(20,5),
    tp              NUMERIC(20,5),
    swap            NUMERIC(20,5) DEFAULT 0,
    profit          NUMERIC(20,5) DEFAULT 0,
    status          TEXT         NOT NULL DEFAULT 'OPEN'
                       CHECK (status IN ('OPEN','CLOSED')),
    opened_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    closed_at       TIMESTAMPTZ,
    close_price     NUMERIC(20,5),
    UNIQUE (ticket)
);
CREATE INDEX IF NOT EXISTS idx_positions_status ON positions(status, symbol);

CREATE TABLE IF NOT EXISTS risk_events (
    id              BIGSERIAL PRIMARY KEY,
    severity        TEXT         NOT NULL CHECK (severity IN ('INFO','WARN','BLOCK','CRITICAL')),
    code            TEXT         NOT NULL,        -- e.g. SPREAD_TOO_HIGH, MAX_DAILY_LOSS
    symbol          TEXT,
    message         TEXT         NOT NULL,
    context         JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_risk_events_time ON risk_events(created_at DESC);

CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGSERIAL PRIMARY KEY,
    actor           TEXT         NOT NULL,        -- scheduler | cli | risk | execution
    action          TEXT         NOT NULL,
    entity          TEXT,
    entity_id       TEXT,
    detail          JSONB,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);
