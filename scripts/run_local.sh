#!/usr/bin/env bash
# Start the full platform locally (no Docker): Python service, C++ gateway, Go
# scheduler. Logs go to logs/. Ctrl-C stops everything.
set -euo pipefail
cd "$(dirname "$0")/.."

[ -f .env ] || cp .env.example .env
set -a; source .env; set +a
mkdir -p logs

echo "[1/3] starting Python MT5/strategy service on ${PYTHON_SERVICE_ADDR}"
( cd python && exec ../.venv/bin/uvicorn service:app \
    --host "${PYTHON_SERVICE_ADDR%%:*}" --port "${PYTHON_SERVICE_ADDR##*:}" ) \
    >logs/python_service.out 2>&1 &
PY_PID=$!

echo "[2/3] waiting for Python health..."
for i in {1..30}; do
  curl -sf "http://${PYTHON_SERVICE_ADDR}/health" >/dev/null && break || sleep 1
done

echo "[3/3] starting C++ execution gateway on ${CPP_GATEWAY_ADDR}"
./cpp/build/execution_gateway >logs/cpp_gateway.out 2>&1 &
CPP_PID=$!
sleep 1

cleanup() { echo; echo "stopping..."; kill $PY_PID $CPP_PID 2>/dev/null || true; }
trap cleanup EXIT INT TERM

echo "starting Go scheduler (trader run). Ctrl-C to stop."
./bin/trader run
