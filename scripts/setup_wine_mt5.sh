#!/usr/bin/env bash
# ============================================================
# Set up the Wine bridge so the MetaTrader5 Python package can drive a real
# Exness MT5 terminal on Ubuntu 24.04.
#
# Architecture:
#   - MT5 terminal64.exe runs under Wine.
#   - A *Windows* Python (installed inside the same Wine prefix) runs our
#     python/service.py, because the MetaTrader5 package only binds to the
#     terminal from a Windows Python process.
#   - The Go and C++ services connect to it over TCP exactly as before.
#
# This script automates the prerequisites; the MT5 + Python-for-Windows
# installers are interactive and must be completed by hand (URLs printed below).
# ============================================================
set -euo pipefail

export WINEPREFIX="${WINEPREFIX:-$HOME/.mt5}"
export WINEARCH=win64

echo "==> Installing Wine (sudo required)"
sudo dpkg --add-architecture i386
sudo apt-get update
sudo apt-get install -y --no-install-recommends wine64 wine32 winbind xvfb

echo "==> Creating Wine prefix at $WINEPREFIX"
wineboot --init || true

cat <<'EOF'

==> MANUAL STEPS (interactive installers):

1. Download the Exness MT5 terminal installer:
     https://www.exness.com/  ->  Download MetaTrader 5
   Install it into the Wine prefix:
     WINEPREFIX=$HOME/.mt5 wine exness_mt5_setup.exe

2. Install Python 3.12 (Windows x86-64) INTO the same Wine prefix:
     https://www.python.org/downloads/windows/
     WINEPREFIX=$HOME/.mt5 wine python-3.12.x-amd64.exe \
         /quiet PrependPath=1 Include_test=0

3. Install the platform deps with the Windows pip:
     WINEPREFIX=$HOME/.mt5 wine python -m pip install \
         MetaTrader5 pandas numpy fastapi "uvicorn[standard]" \
         pydantic pydantic-settings loguru python-dotenv

4. Launch MT5 once, log into your Exness account, enable
   "Allow Algo Trading", and keep the terminal running.

5. Start the Python service with the WINDOWS python so it can attach to MT5:
     WINEPREFIX=$HOME/.mt5 wine python service.py
   (run headless with: xvfb-run -a WINEPREFIX=$HOME/.mt5 wine python service.py)

After that, the Go scheduler and C++ gateway talk to it unchanged. Verify with:
     ./bin/trader connect
The "backend" should read "mt5" instead of "simulation".
EOF
