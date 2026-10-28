"""XAUUSD discretionary scalping engine.

Self-contained state machine (see state_machine.ScalperStateMachine) that is
intentionally separate from services/scheduler's one-shot per-tick EMA/RSI
model: it actively manages an open position (quick exits, re-entry, direction
flips) rather than firing a single BUY/SELL/HOLD signal per tick.

Modules:
    types          -- shared enums/dataclasses
    config         -- SCALPER_-prefixed settings (additive to python/config.py)
    direction      -- rule-based direction detection (structure/momentum/volatility)
    entry          -- entry confirmation (spread/chop/last-bar filters)
    exit           -- exit logic (risk stop/target/reversal/fade/max-hold)
    reentry        -- same-direction vs direction-switch classification
    risk_guards    -- cooldown, spread filter, loss caps, kill-switch
    state_machine  -- orchestrates the above into the FLAT/LONG/SHORT loop
    broker_adapter -- live-only: maps state-machine actions onto mt5.client.Broker
    backtest       -- offline bar-replay + statistics report
"""
