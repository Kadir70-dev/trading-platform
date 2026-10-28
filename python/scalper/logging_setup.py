"""Rotating-file logging for 24/7 operation.

A size-based RotatingFileHandler so logs never fill the VPS disk. Works
identically on Windows and POSIX. Pure stdlib; offline-testable.
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path


def build_rotating_logger(name: str, log_path, *, max_bytes: int = 5_000_000,
                          backup_count: int = 10, level: int = logging.INFO) -> logging.Logger:
    """Logger writing JSON-friendly lines to `log_path`, rotating at
    `max_bytes` and keeping `backup_count` compressed-by-count backups."""
    log_path = Path(log_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False
    # Avoid duplicate handlers on re-init (restart within same process/tests).
    for h in list(logger.handlers):
        if isinstance(h, RotatingFileHandler) and getattr(h, "_scalper_path", None) == str(log_path):
            return logger
    handler = RotatingFileHandler(log_path, maxBytes=max_bytes, backupCount=backup_count,
                                  encoding="utf-8")
    handler._scalper_path = str(log_path)  # type: ignore[attr-defined]
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    return logger
