"""Configuration for the XAUUSD discretionary scalper.

Fully additive to python/config.py: reads the same .env file with a
SCALPER_-prefixed namespace, so it can't collide with or mutate the existing
Settings model used by the Go-scheduler-driven strategies.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ScalperParams(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore",
        env_ignore_empty=True,
    )

    symbol: str = Field("XAUUSD", alias="SCALPER_SYMBOL")
    lot_size: float = Field(0.01, alias="SCALPER_LOT_SIZE")
    magic_number: int = Field(20260916, alias="SCALPER_MAGIC_NUMBER")

    # Direction detection (structure / momentum / volatility)
    structure_lookback: int = Field(20, alias="SCALPER_STRUCTURE_LOOKBACK")
    momentum_period: int = Field(5, alias="SCALPER_MOMENTUM_PERIOD")
    atr_period: int = Field(14, alias="SCALPER_ATR_PERIOD")
    chop_atr_floor_pct: float = Field(40.0, alias="SCALPER_CHOP_ATR_FLOOR_PCT")
    # What counts as a "confirmed" direction (used for both entries and
    # exit.py's reversal detection — the same classification feeds both).
    direction_strong_threshold: float = Field(60.0, alias="SCALPER_DIRECTION_STRONG_THRESHOLD")
    direction_weak_threshold: float = Field(20.0, alias="SCALPER_DIRECTION_WEAK_THRESHOLD")

    # Entry / exit. max_spread_points/stop_distance_usd calibrated against
    # actual Dukascopy XAUUSD M1 history (2019-2025): median spread ~36 points
    # (~$0.36, so a round trip costs ~$0.72), median ATR(14) ~$0.50/oz. The
    # stop distance must clear the round-trip spread cost by a real margin or
    # the strategy just pays the spread on noise — $5 (the original guess) was
    # 10x too wide to ever behave like a "quick scalp"; $0.40 was so tight it
    # sat inside the spread itself.
    max_spread_points: float = Field(60.0, alias="SCALPER_MAX_SPREAD_POINTS")
    momentum_fade_threshold: float = Field(0.3, alias="SCALPER_MOMENTUM_FADE_THRESHOLD")
    max_hold_bars: int = Field(240, alias="SCALPER_MAX_HOLD_BARS")  # a fallback ceiling, not the exit driver

    # stop_distance_usd is a price distance ($ per oz of XAUUSD movement) —
    # deliberately independent of lot_size. This is what "fixed lot size" +
    # "no scaling" requires: the state machine must enter/exit at the same
    # price levels regardless of how many lots are on the trade, so changing
    # lot_size only scales dollar P&L, never *when* the strategy trades. It
    # now defines the unit ("1R") that trailing/emergency-stop distances are
    # expressed in, rather than being a fixed stop itself.
    stop_distance_usd: float = Field(1.00, alias="SCALPER_STOP_DISTANCE_USD")

    # ---- Discretionary flip behavior (exit.py v2) ----
    # A losing position is cut on the FIRST *confirmed* opposite-direction
    # signal rather than waiting for a wide stop; "confirmed" means the
    # opposite-strong reading has held for this many consecutive bars, so a
    # single noisy tick can't flip the bot.
    min_reversal_confirm_bars: int = Field(2, alias="SCALPER_MIN_REVERSAL_CONFIRM_BARS")
    # A winning position lets profit run: trailing only arms once favorable
    # move >= trail_activate_r * risk_unit, then exits if price gives back
    # >= trail_giveback_pct of the peak favorable move.
    trail_activate_r: float = Field(1.0, alias="SCALPER_TRAIL_ACTIVATE_R")
    trail_giveback_pct: float = Field(0.4, alias="SCALPER_TRAIL_GIVEBACK_PCT")
    # Hard catastrophic backstop (both live broker-side SL and the Python
    # check) — deliberately much wider than the signal-based cut above; it
    # exists for gap/spike risk, not as the normal exit path.
    emergency_stop_r_mult: float = Field(3.0, alias="SCALPER_EMERGENCY_STOP_R_MULT")
    # Anti-churn: caps how many close+flip round trips can happen per hour,
    # independent of the normal cooldown (a confirmed flip is allowed to
    # bypass the cooldown — it's a deliberate signal, not overtrading — but
    # must still respect this ceiling).
    max_flips_per_hour: int = Field(6, alias="SCALPER_MAX_FLIPS_PER_HOUR")

    # Risk guards (these ARE lot-size-dependent dollar caps, by design)
    cooldown_sec: int = Field(60, alias="SCALPER_COOLDOWN_SEC")
    max_daily_loss_usd: float = Field(30.0, alias="SCALPER_MAX_DAILY_LOSS_USD")
    max_consecutive_losses: int = Field(4, alias="SCALPER_MAX_CONSECUTIVE_LOSSES")

    # ---- Hedging overlay (hedge.py) — a risk-management layer, not martingale.
    # Never increases primary size, never averages down; at most one hedge
    # layer, at a fixed configured ratio of the primary's fixed lot.
    hedge_enabled: bool = Field(False, alias="SCALPER_HEDGE_ENABLED")
    hedge_ratio: float = Field(0.50, alias="SCALPER_HEDGE_RATIO")  # 0.25 / 0.50 / 1.00 of primary lot
    hedge_max_layers: int = Field(1, alias="SCALPER_HEDGE_MAX_LAYERS")
    # Price distance ($/oz), independent of lot size — same reasoning as
    # stop_distance_usd: hedging decisions are about price movement, not $.
    hedge_trigger_adverse_usd: float = Field(0.50, alias="SCALPER_HEDGE_TRIGGER_ADVERSE_USD")
    hedge_stop_distance_usd: float = Field(1.00, alias="SCALPER_HEDGE_STOP_DISTANCE_USD")
    hedge_volatility_spike_ratio: float = Field(1.5, alias="SCALPER_HEDGE_VOLATILITY_SPIKE_RATIO")
    # Dollar cap on combined primary+hedge P&L — genuinely lot-size-dependent,
    # like max_daily_loss_usd, since it caps total account impact.
    hedge_combined_loss_usd: float = Field(54.0, alias="SCALPER_HEDGE_COMBINED_LOSS_USD")

    # XAUUSD contract math: $ P&L per 1.0 price-unit move per 1.0 lot.
    # Standard MT5 XAUUSD contract = 100 oz/lot, so a $1/oz move on 1 lot = $100.
    contract_size: float = Field(100.0, alias="SCALPER_CONTRACT_SIZE")
    point_size: float = Field(0.01, alias="SCALPER_POINT_SIZE")  # XAUUSD point = $0.01
    leverage: int = Field(2000, alias="LEVERAGE")  # for margin-usage reporting only

    @property
    def max_loss_per_trade_usd(self) -> float:
        """Dollar loss if the stop is hit, at the configured fixed lot size."""
        return self.stop_distance_usd * self.lot_size * self.contract_size

    def margin_required(self, price: float) -> float:
        """Approximate margin to hold one fixed-lot position at `price`."""
        return (self.lot_size * self.contract_size * price) / max(self.leverage, 1)


@lru_cache
def get_scalper_params() -> ScalperParams:
    return ScalperParams()
