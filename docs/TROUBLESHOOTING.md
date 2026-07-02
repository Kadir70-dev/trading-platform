# Troubleshooting

## `/health` shows `"backend":"simulation"` but I want live trading
The `MetaTrader5` package or terminal isn't reachable from the Python process.
- Ensure the service runs under the **Windows** Python in the Wine prefix, not the
  Linux venv (see [MT5_LINUX_WINE.md](MT5_LINUX_WINE.md)).
- Ensure the MT5 terminal is **open and logged in**, with Algo Trading enabled.

## `trader connect` → connection refused
The Python service isn't up. Start it (`run_local.sh` or uvicorn) and confirm
`curl http://127.0.0.1:8800/health`. Check `PYTHON_SERVICE_ADDR` matches.

## `[exec] C++ gateway unavailable, using direct Python executor`
The Go layer couldn't reach the gateway on `CPP_GATEWAY_ADDR`, so it fell back to
sending orders straight to Python. Start the gateway (`cpp/build/execution_gateway`)
or accept the fallback (validation/latency monitoring is then skipped).

## Orders rejected with `INVALID_STOPS` / `requires sl < tp`
The broker enforces minimum stop distances and correct stop sides. For very tight
ATR on M1, widen `SL_ATR_MULT` or use a higher timeframe. BUY needs `sl < price < tp`,
SELL needs `tp < price < sl`.

## Trade blocked: `RISK_TOO_SMALL`
`MAX_RISK_PCT` of equity is too little to buy even `volume_min` at the current SL
distance (common with a tiny balance + wide stop). Increase equity, reduce the SL
distance, or raise `MAX_RISK_PCT` (carefully).

## Trade blocked: `INSUFFICIENT_MARGIN`
Required margin `volume × contract × price / leverage` exceeds free margin. Lower the
volume/risk, reduce open trades, or confirm leverage is actually 1:2000
(`trader connect`).

## Trading halted mid-session
A circuit breaker tripped — check `logs/risk_events.jsonl` (or the `risk_events`
table) for `MAX_DAILY_LOSS`, `MAX_DRAWDOWN`, or `EMERGENCY_STOP`. Daily-loss resets
at UTC midnight; drawdown is peak-to-trough on equity.

## `go build -tags postgres` fails
You need the driver: `go get github.com/jackc/pgx/v5` (network access required).
The default build (no tag) uses the file store and needs nothing.

## C++ gateway won't bind
Another process holds `CPP_GATEWAY_ADDR`. Find it: `ss -ltnp | grep 8900`. Change
the port in `.env` if needed.

## Leverage mismatch warning
`LEVERAGE` in `.env` differs from what the broker reports. The platform trusts the
broker value and only warns. Update `.env` to match, or change leverage in the
Exness Personal Area.

## Backtest prints nothing / errors
Run it from the venv: `make py-test` deps must be installed, and
`trader backtest` shells into `python/` — ensure `.venv` exists and
`python/requirements.txt` is installed.
