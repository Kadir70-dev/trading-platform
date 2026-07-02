#!/usr/bin/env bash
# Install build prerequisites on Ubuntu 24.04 LTS and build the whole platform.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Installing system packages (sudo required)"
sudo apt-get update
sudo apt-get install -y --no-install-recommends \
    build-essential cmake pkg-config \
    python3 python3-venv python3-pip \
    git curl ca-certificates postgresql-client

# Go 1.22+ (skip if already present and new enough)
if ! command -v go >/dev/null 2>&1; then
  echo "==> Installing Go 1.22"
  GO_VER=1.22.5
  curl -fsSL "https://go.dev/dl/go${GO_VER}.linux-amd64.tar.gz" -o /tmp/go.tgz
  sudo rm -rf /usr/local/go && sudo tar -C /usr/local -xzf /tmp/go.tgz
  export PATH=$PATH:/usr/local/go/bin
  echo 'export PATH=$PATH:/usr/local/go/bin' >> "$HOME/.bashrc"
fi

echo "==> Building C++ gateway"
cmake -S cpp -B cpp/build -DCMAKE_BUILD_TYPE=Release
cmake --build cpp/build -j

echo "==> Building Go trader"
go build -o bin/trader ./cmd/trader

echo "==> Python venv + deps"
python3 -m venv .venv
./.venv/bin/pip install -q --upgrade pip
# MetaTrader5 is Windows-only; skip on Linux (simulation backend is used unless
# you set up the Wine bridge — see scripts/setup_wine_mt5.sh).
grep -v '^MetaTrader5' python/requirements.txt > /tmp/req.linux.txt
./.venv/bin/pip install -q -r /tmp/req.linux.txt

[ -f .env ] || cp .env.example .env

echo "==> Done. Next:"
echo "   1) edit .env with your Exness MT5 credentials"
echo "   2) ./scripts/run_local.sh   (or: docker compose up --build)"
