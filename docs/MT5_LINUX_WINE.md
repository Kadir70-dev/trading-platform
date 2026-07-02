# Running real Exness MT5 on Ubuntu 24.04 (Wine bridge)

The `MetaTrader5` Python package ships only a Windows binary. To trade for real on
Linux you run the **MT5 terminal** and a **Windows Python** under Wine; our
`python/service.py` runs in that Windows Python and attaches to the terminal. The
Go and C++ services are unaffected — they just talk HTTP/TCP to the service.

> Prefer not to use Wine? Run the Python service on a small Windows VM/host and
> point `PYTHON_SERVICE_ADDR` at it. Everything else stays on Linux.

## Automated prerequisites

```bash
./scripts/setup_wine_mt5.sh
```

This installs Wine and creates a prefix at `~/.mt5`. The MT5 and Python-for-Windows
installers are interactive; the script prints the exact commands.

## Manual steps (summary)

```bash
export WINEPREFIX=$HOME/.mt5 WINEARCH=win64

# 1. Install the Exness MT5 terminal
wine exness_mt5_setup.exe

# 2. Install Windows Python 3.12 into the same prefix
wine python-3.12.x-amd64.exe /quiet PrependPath=1

# 3. Install platform deps with the Windows pip
wine python -m pip install MetaTrader5 pandas numpy fastapi "uvicorn[standard]" \
    pydantic pydantic-settings loguru python-dotenv

# 4. Launch MT5, log into Exness, enable Tools → Options → Expert Advisors →
#    "Allow Algo Trading", keep it running.

# 5. Start the service with the WINDOWS python (headless via xvfb):
cd python
xvfb-run -a env WINEPREFIX=$HOME/.mt5 wine python service.py
```

## Verify the live backend

```bash
curl -s http://127.0.0.1:8800/health
# {"status":"ok","backend":"mt5",...}     ← "mt5", not "simulation"

trader connect
# Broker: Exness   Server: Exness-...   Leverage: 1:2000   Mode: DEMO/REAL
```

## How fallback works

If the `MetaTrader5` import fails or the terminal isn't reachable, `build_broker()`
in [`python/mt5/client.py`](../python/mt5/client.py) logs a warning and returns the
**simulation** backend. This is why the platform runs on plain Ubuntu and in Docker
for development and testing — but only the `mt5` backend places real orders.

## Tips

- Run MT5 + the service under a persistent session (`tmux`/`systemd`), not a login shell.
- Keep the terminal logged in; MT5 auto-reconnects, and the service surfaces
  disconnects as `MT5_DISCONNECTED` risk events.
- One terminal per Python process. For multiple accounts, run multiple prefixes +
  services on different ports.
