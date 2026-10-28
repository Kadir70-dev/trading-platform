"""Single-instance process guard (cross-platform: Windows + POSIX).

Prevents a second scalper process from running against the same account — the
key safeguard so a VPS reboot or a Task Scheduler double-fire can never produce
duplicate entries. A stale lock (owning PID no longer alive) is reclaimed.

Pure-filesystem + PID liveness; no trading involvement, fully offline-testable.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":  # Windows
            import ctypes  # local import so POSIX tests don't need it
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            h = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not h:
                return False
            ctypes.windll.kernel32.CloseHandle(h)
            return True
        os.kill(pid, 0)   # POSIX: signal 0 just checks existence
        return True
    except (OSError, ProcessLookupError, PermissionError) as exc:
        # PermissionError => process exists but owned by another user.
        return isinstance(exc, PermissionError)
    except Exception:
        return False


@dataclass
class LockResult:
    acquired: bool
    reason: str
    owner_pid: int | None = None


class SingleInstanceLock:
    """Acquire via a PID lockfile. `pid_alive` is injectable for tests."""

    def __init__(self, lock_path, pid: int | None = None, pid_alive=_pid_alive):
        self.path = Path(lock_path)
        self.pid = pid if pid is not None else os.getpid()
        self._pid_alive = pid_alive
        self._held = False

    def acquire(self) -> LockResult:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            try:
                owner = int(self.path.read_text().strip() or "0")
            except (ValueError, OSError):
                owner = 0
            if owner and owner != self.pid and self._pid_alive(owner):
                return LockResult(False, "ALREADY_RUNNING", owner)
            # stale (dead owner) or ours — reclaim
        try:
            self.path.write_text(str(self.pid))
        except OSError as exc:
            return LockResult(False, f"LOCK_WRITE_FAILED:{exc}")
        self._held = True
        return LockResult(True, "ACQUIRED", self.pid)

    def release(self) -> None:
        if not self._held:
            return
        try:
            if self.path.exists() and int(self.path.read_text().strip() or "0") == self.pid:
                self.path.unlink()
        except (OSError, ValueError):
            pass
        self._held = False

    def __enter__(self):
        return self.acquire()

    def __exit__(self, *exc):
        self.release()
