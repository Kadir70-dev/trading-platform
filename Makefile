# ============================================================
# Exness MT5 Trading Platform — build & ops
# ============================================================
.PHONY: help all go-build go-test cpp-build py-setup py-test test \
        run-python run-gateway run-trader db-init clean fmt

SHELL := /bin/bash
PY := python3
VENV := .venv
BIN := bin/trader
GATEWAY := cpp/build/execution_gateway

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n",$$1,$$2}'

all: go-build cpp-build py-setup ## Build everything

# ---- Go ----
go-build: ## Build the trader binary (offline, stdlib only)
	go build -o $(BIN) ./cmd/trader
	@echo "built $(BIN)"

go-build-pg: ## Build with PostgreSQL store (requires: go get github.com/jackc/pgx/v5)
	go build -tags postgres -o $(BIN) ./cmd/trader

go-test: ## Run Go tests
	go test ./...

# ---- C++ ----
cpp-build: ## Build the C++ execution gateway
	cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
	cmake --build cpp/build -j

# ---- Python ----
py-setup: ## Create venv and install Python deps
	$(PY) -m venv $(VENV)
	$(VENV)/bin/pip install -q --upgrade pip
	$(VENV)/bin/pip install -q -r python/requirements.txt

py-test: ## Run Python tests
	cd python && ../$(VENV)/bin/python -m pytest tests/ -q

test: go-test py-test ## Run all tests

# ---- Run (3 terminals, or use docker-compose) ----
run-python: ## Start the Python MT5/strategy service
	cd python && ../$(VENV)/bin/uvicorn service:app --host 0.0.0.0 --port 8800

run-gateway: ## Start the C++ execution gateway
	set -a; source .env; set +a; $(GATEWAY)

run-trader: ## Start the Go scheduler (live loop)
	$(BIN) run

db-init: ## Apply the PostgreSQL schema (needs psql + DATABASE_URL)
	psql "$$DATABASE_URL" -f scripts/schema.sql

fmt: ## Format Go code
	go fmt ./...

clean: ## Remove build artifacts
	rm -rf bin cpp/build logs/*.jsonl logs/*.log
