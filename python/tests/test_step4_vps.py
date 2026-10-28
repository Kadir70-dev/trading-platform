"""Step 4: VPS readiness — market sessions, Telegram, process guard, log
rotation, and safe position adoption. All offline. No orders, no network."""
import logging
from datetime import datetime, timezone

import pytest

from scalper import execution_safety as es
from scalper import sessions as sess
from scalper.config import ScalperParams
from scalper.exit import ExitEvaluator
from scalper.logging_setup import build_rotating_logger
from scalper.notify import TelegramNotifier
from scalper.process_guard import SingleInstanceLock
from scalper.types import DirectionReading, Direction, PositionState


def _utc(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


# ============================ market sessions ==============================
def test_session_open_midweek():
    assert sess.entries_allowed(_utc(2026, 9, 16, 12, 0))      # Wed noon


def test_session_closed_saturday():
    assert not sess.entries_allowed(_utc(2026, 9, 19, 12, 0))  # Saturday


def test_session_sunday_opens_at_2200():
    assert not sess.entries_allowed(_utc(2026, 9, 20, 21, 0))  # Sun 21:00 still closed
    assert sess.entries_allowed(_utc(2026, 9, 20, 22, 0))      # Sun 22:00 open


def test_session_friday_closes_at_2100():
    assert sess.entries_allowed(_utc(2026, 9, 18, 20, 0))      # Fri 20:00 open
    assert not sess.entries_allowed(_utc(2026, 9, 18, 21, 0))  # Fri 21:00 closed


def test_session_daily_break_blocks_entries():
    # Wed 21:00–22:00 UTC maintenance break
    assert not sess.entries_allowed(_utc(2026, 9, 16, 21, 30))
    assert sess.entries_allowed(_utc(2026, 9, 16, 22, 30))


def test_session_naive_datetime_treated_as_utc():
    assert sess.entries_allowed(datetime(2026, 9, 16, 12, 0))  # no tzinfo


# ============================ Telegram =====================================
def _recording_transport(log):
    def t(token, chat, text):
        log.append((token, chat, text))
        return True
    return t


def test_telegram_disabled_without_credentials(monkeypatch):
    monkeypatch.delenv("SCALPER_TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SCALPER_TELEGRAM_CHAT_ID", raising=False)
    n = TelegramNotifier.from_env()
    assert not n.enabled and n.send("hi") is False      # no-op, no crash


def test_telegram_sends_when_configured():
    log = []
    n = TelegramNotifier(token="T", chat_id="C", transport=_recording_transport(log))
    assert n.order_executed("BUY", 0.01, 2000.0, 123)
    assert log and "ORDER BUY" in log[0][2]
    assert n.token not in "".join(x[2] for x in log)    # creds never in message text


def test_telegram_failure_never_raises_and_is_counted():
    def boom(token, chat, text):
        raise ConnectionError("telegram down")
    n = TelegramNotifier(token="T", chat_id="C", transport=boom)
    assert n.startup("XAUUSDm", "DEMO", 1000.0) is False  # swallowed
    assert n.failures == 1


def test_telegram_unconfirmed_pnl_is_labelled_not_fabricated():
    log = []
    n = TelegramNotifier(token="T", chat_id="C", transport=_recording_transport(log))
    n.position_closed(ticket=9, realized=None, confirmed=False)
    assert "UNCONFIRMED" in log[0][2] and "unknown" in log[0][2]
    n.position_closed(ticket=9, realized=-30.0, confirmed=True)
    assert "broker-confirmed" in log[1][2] and "-30.00" in log[1][2]


# ============================ process guard ================================
def test_single_instance_blocks_duplicate(tmp_path):
    lockfile = tmp_path / "scalper.lock"
    a = SingleInstanceLock(lockfile, pid=1001, pid_alive=lambda p: True)
    b = SingleInstanceLock(lockfile, pid=1002, pid_alive=lambda p: True)
    assert a.acquire().acquired
    r = b.acquire()
    assert not r.acquired and r.reason == "ALREADY_RUNNING" and r.owner_pid == 1001


def test_stale_lock_is_reclaimed_after_crash(tmp_path):
    lockfile = tmp_path / "scalper.lock"
    lockfile.write_text("4242")                          # dead previous owner
    b = SingleInstanceLock(lockfile, pid=5555, pid_alive=lambda p: p == 5555)
    r = b.acquire()
    assert r.acquired and lockfile.read_text() == "5555"  # reclaimed


def test_lock_release_removes_file(tmp_path):
    lockfile = tmp_path / "scalper.lock"
    lk = SingleInstanceLock(lockfile, pid=7, pid_alive=lambda p: True)
    lk.acquire(); lk.release()
    assert not lockfile.exists()


def test_reboot_then_restart_single_owner(tmp_path):
    """VPS reboot: old PID dead, new process acquires cleanly -> one owner."""
    lockfile = tmp_path / "scalper.lock"
    SingleInstanceLock(lockfile, pid=100, pid_alive=lambda p: True).acquire()
    # reboot: pid 100 no longer alive
    new = SingleInstanceLock(lockfile, pid=200, pid_alive=lambda p: p == 200)
    assert new.acquire().acquired and lockfile.read_text() == "200"


# ============================ log rotation =================================
def test_log_rotation_creates_backups(tmp_path):
    logf = tmp_path / "scalper.log"
    log = build_rotating_logger("scalper_test_rot", logf, max_bytes=1000, backup_count=3)
    for i in range(500):
        log.info("x" * 50 + f" line {i}")
    for h in log.handlers:
        h.flush()
    backups = list(tmp_path.glob("scalper.log*"))
    assert len(backups) >= 2 and len(backups) <= 4       # base + rotated, capped
    for h in list(log.handlers):
        h.close(); log.removeHandler(h)


def test_log_setup_idempotent(tmp_path):
    logf = tmp_path / "s.log"
    l1 = build_rotating_logger("scalper_idem", logf)
    l2 = build_rotating_logger("scalper_idem", logf)
    assert l1 is l2 and len(l1.handlers) == 1             # no duplicate handlers
    for h in list(l1.handlers):
        h.close(); l1.removeHandler(h)


# ===================== safe adoption (holding duration) ====================
def _params():
    p = ScalperParams(_env_file=None)
    p.symbol = "XAUUSDm"
    return p


def test_adopt_sets_holding_duration_to_zero_no_spurious_max_hold():
    """Regression: a naive adoption with entry_bar_index=0 against a large live
    bar index makes bars_held huge and instantly trips EXIT_MAX_HOLD_TIME. The
    safe adoption must start bars_held at 0."""
    p = _params()
    broker_pos = {"side": "BUY", "entry_price": 4000.0, "ticket": 555}
    bar_index = 5000                                     # large live index
    pos = es.adopt_position(p, broker_pos, bar_index, entry_time=None)
    assert pos.entry_bar_index == bar_index              # not 0
    bars_held = bar_index - pos.entry_bar_index
    assert bars_held == 0 and bars_held < p.max_hold_bars

    # An exit evaluation on the adoption bar must NOT be EXIT_MAX_HOLD_TIME.
    ev = ExitEvaluator(p)
    reading = DirectionReading(Direction.LONG_STRONG, 80.0, "ok", atr_ratio=1.0,
                               momentum_norm=1.0, structure_score=1.0)
    xd = ev.evaluate(pos, price=4000.5, reading=reading, momentum_norm=1.0, bar_index=bar_index)
    assert xd.reason_code != "EXIT_MAX_HOLD_TIME"


def test_adopt_direction_and_ticket_mapped():
    p = _params()
    pos = es.adopt_position(p, {"side": "SELL", "entry_price": 4010.0, "ticket": 9}, 10)
    assert pos.direction == PositionState.SHORT and pos.ticket == 9
    assert pos.risk_unit == p.stop_distance_usd
