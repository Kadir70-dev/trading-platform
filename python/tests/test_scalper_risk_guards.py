"""RiskGuard tests: cooldown, spread filter, daily loss, consecutive losses, kill-switch."""
from datetime import datetime, timedelta, timezone

from scalper.config import ScalperParams
from scalper.risk_guards import RiskGuard


def _params(**overrides):
    p = ScalperParams(_env_file=None)
    for k, v in overrides.items():
        setattr(p, k, v)
    return p


def test_cooldown_blocks_immediate_reentry():
    g = RiskGuard(_params(cooldown_sec=60))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_trade_result(-1.0, now)
    r = g.check_pretrade(spread_points=1.0, now=now + timedelta(seconds=10))
    assert not r.ok and r.reason_code == "BLOCKED_COOLDOWN"


def test_cooldown_clears_after_interval():
    g = RiskGuard(_params(cooldown_sec=60))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_trade_result(-1.0, now)
    r = g.check_pretrade(spread_points=1.0, now=now + timedelta(seconds=61))
    assert r.ok


def test_spread_filter_blocks_wide_spread():
    g = RiskGuard(_params(max_spread_points=10.0))
    r = g.check_pretrade(spread_points=20.0, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert not r.ok and r.reason_code == "BLOCKED_SPREAD_FILTER"


def test_max_daily_loss_trips_kill_switch():
    g = RiskGuard(_params(max_daily_loss_usd=10.0, max_consecutive_losses=99))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_trade_result(-11.0, now)
    r = g.check_pretrade(spread_points=1.0, now=now)
    assert not r.ok and r.reason_code == "BLOCKED_KILL_SWITCH" and g.kill_switch


def test_max_consecutive_losses_trips_kill_switch():
    g = RiskGuard(_params(max_consecutive_losses=3, max_daily_loss_usd=1000.0, cooldown_sec=0))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    for _ in range(3):
        g.register_trade_result(-1.0, now)
    r = g.check_pretrade(spread_points=1.0, now=now)
    assert not r.ok and r.reason_code == "BLOCKED_KILL_SWITCH"


def test_daily_pnl_resets_on_new_day():
    g = RiskGuard(_params(max_daily_loss_usd=10.0, cooldown_sec=0))
    day1 = datetime(2026, 1, 1, tzinfo=timezone.utc)
    day2 = datetime(2026, 1, 2, tzinfo=timezone.utc)
    g.register_trade_result(-5.0, day1)
    assert g.daily_pnl == -5.0
    g.check_pretrade(spread_points=1.0, now=day2)
    assert g.daily_pnl == 0.0


def test_manual_kill_switch_blocks_trading():
    g = RiskGuard(_params())
    g.trip_kill_switch("manual stop")
    r = g.check_pretrade(spread_points=1.0, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
    assert not r.ok and r.reason_code == "BLOCKED_KILL_SWITCH" and r.detail == "manual stop"


def test_flip_allowed_bypasses_ordinary_cooldown():
    g = RiskGuard(_params(cooldown_sec=999999, max_flips_per_hour=6))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_trade_result(-1.0, now)  # would normally block check_pretrade for a very long time
    r = g.check_flip_allowed(now + timedelta(seconds=1))
    assert r.ok


def test_flip_frequency_ceiling_blocks_repeated_flips():
    g = RiskGuard(_params(max_flips_per_hour=2))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_flip(now)
    g.register_flip(now + timedelta(minutes=10))
    r = g.check_flip_allowed(now + timedelta(minutes=20))
    assert not r.ok and r.reason_code == "BLOCKED_MAX_FLIP_FREQUENCY"


def test_flip_frequency_ceiling_clears_after_the_hour_window():
    g = RiskGuard(_params(max_flips_per_hour=1))
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    g.register_flip(now)
    r = g.check_flip_allowed(now + timedelta(hours=1, minutes=1))
    assert r.ok
