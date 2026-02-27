"""Cross-thread runtime stop requests (tray icon, Telegram command, etc.)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_STOP_CALLBACK: Callable[[str], None] | None = None


def register_stop_callback(callback: Callable[[str], None]) -> None:
    with _LOCK:
        global _STOP_CALLBACK
        _STOP_CALLBACK = callback


def clear_stop_callback() -> None:
    with _LOCK:
        global _STOP_CALLBACK
        _STOP_CALLBACK = None


def request_stop(reason: str = "external") -> None:
    callback: Callable[[str], None] | None
    with _LOCK:
        callback = _STOP_CALLBACK
    if callback is None:
        return
    try:
        callback(reason)
    except Exception as exc:
        logger.exception("Stop callback failed (%s): %s", reason, exc)
