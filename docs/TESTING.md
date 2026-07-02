# Testing Guide

```bash
make test                     # everything
go test ./...                 # Go unit tests
cd python && pytest tests/    # Python unit tests
```

## What's covered

**Go — risk engine** (`services/risk/risk_test.go`, 10 tests):
valid-trade approval, position sizing from `MAX_RISK_PCT`, and blocks for high
spread, missing stops, low reward:risk, duplicate position, max open trades,
insufficient margin, emergency stop, and the daily-loss breaker.

**Python — indicators** (`python/tests/test_indicators.py`, 7 tests):
EMA on constant/trending series, RSI bounds and the all-gains→100 / all-losses→0
edge cases, ATR positivity, and `enrich` column contract.

**Python — strategy** (`python/tests/test_strategy.py`, 5 tests):
uptrend→BUY, downtrend→SELL, stop ordering (BUY `sl<price<tp`, SELL `tp<price<sl`),
reward:risk ≥ target, low-confidence/insufficient-data → HOLD.

## Integration smoke test (sim backend, no MT5)

```bash
# terminal 1
cd python && ../.venv/bin/uvicorn service:app --port 8800
# terminal 2
set -a; source .env; set +a; ./cpp/build/execution_gateway
# terminal 3
./bin/trader connect
./bin/trader strategy XAUUSD
./bin/trader buy XAUUSD -lot 0.05      # Go → C++ gateway → Python, fill confirmed
./bin/trader positions
./bin/trader close-all
```

`{"cmd":"ping"}` to the gateway returns routed/filled/rejected counts and p50/p99
latency for the execution path.

## Backtesting

```bash
trader backtest -sym XAUUSD -tf M5 -bars 5000
# or directly:
cd python && ../.venv/bin/python backtest_cli.py --symbol EURUSD --timeframe M15 --bars 8000
```

Reports trades, win rate, profit factor, expectancy, max drawdown, Sharpe proxy,
and return %. The bar-by-bar walk-forward has no look-ahead.
```
