# Exness Setup — Account, 1:2000 Leverage, Demo & Real

## 1. Create an Exness account

1. Register at <https://www.exness.com/>.
2. Verify identity (required for Real accounts; Demo works immediately).
3. In the Personal Area, create a trading account:
   - **Platform:** MetaTrader 5
   - **Account type:** Standard
   - **Currency:** USD (matches the platform defaults; AED works too)

You can create a **Demo** and a **Real** account side by side and switch between
them by changing only `.env`.

## 2. Set leverage to 1:2000

Exness offers leverage up to 1:Unlimited on eligible accounts; 1:2000 is available
broadly on Standard accounts.

1. Personal Area → your trading account → **Settings** → **Change leverage**.
2. Select **1:2000** and confirm.
3. The platform **reads and verifies** leverage from MT5 at startup
   (`trader connect`). A mismatch with `LEVERAGE` in `.env` is logged as a warning,
   not silently ignored.

> Leverage caps margin requirements; it does **not** change your risk per trade.
> Risk is controlled by `MAX_RISK_PCT` and mandatory stop losses.

## 3. Get your credentials

From the Personal Area (or the MT5 "Open an account" confirmation) collect:

| Field | Example | `.env` key |
|-------|---------|-----------|
| Login | `123456789` | `MT5_LOGIN` |
| Password | (trader password, not investor) | `MT5_PASSWORD` |
| Server | `Exness-MT5Trial15` (Demo) / `Exness-MT5Real8` (Real) | `MT5_SERVER` |

The exact server name is shown in MT5 under **File → Login to Trade Account**, and
in your account confirmation email.

## 4. Configure the platform

```dotenv
BROKER=EXNESS
ACCOUNT_MODE=DEMO            # DEMO or REAL
MT5_LOGIN=123456789
MT5_PASSWORD=your_trader_password
MT5_SERVER=Exness-MT5Trial15
LEVERAGE=2000
```

Verify:

```bash
trader connect      # prints broker, server, account mode, leverage, balance, equity
```

## 5. Demo → Real migration

Migrating is a configuration change, not a code change:

1. Validate thoroughly on Demo (`trader run`, review `logs/`, run a backtest).
2. Create/fund the Real account, set 1:2000 leverage.
3. In `.env` set:
   ```dotenv
   ACCOUNT_MODE=REAL
   MT5_LOGIN=<real login>
   MT5_PASSWORD=<real password>
   MT5_SERVER=<real server>
   ```
4. **Start conservative:** `DEFAULT_LOT=0.01`, `MAX_RISK_PCT=1.0`,
   `MAX_OPEN_TRADES=1`, `MAX_DAILY_LOSS_PCT=2.0` for the first sessions.
5. Confirm `trader connect` shows `Mode: REAL` and the correct leverage/balance
   before enabling `trader run`.

The platform refuses to trade if `ACCOUNT_MODE` does not match the broker-reported
mode, so a stale Demo config can't accidentally hit a Real account.
