"""Filesystem persistence helpers."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from companion.core.config import AUDIT_LOG, AUDIT_MAX_BYTES, PROJECTS_FILE

logger = logging.getLogger(__name__)

_projects_cache: dict[str, str] | None = None
_projects_mtime: float | None = None


def load_projects() -> dict[str, str]:
    global _projects_cache, _projects_mtime
    try:
        mtime = PROJECTS_FILE.stat().st_mtime
    except OSError:
        _projects_cache = {}
        _projects_mtime = None
        return {}

    if _projects_cache is not None and mtime == _projects_mtime:
        return dict(_projects_cache)

    try:
        data = json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    _projects_cache = data
    _projects_mtime = mtime
    return dict(data)


def save_projects(projects: dict[str, str]) -> None:
    global _projects_cache, _projects_mtime
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")
    _projects_cache = dict(projects)
    try:
        _projects_mtime = PROJECTS_FILE.stat().st_mtime
    except OSError:
        _projects_mtime = None


def _rotate_audit_if_needed() -> None:
    try:
        if AUDIT_LOG.stat().st_size < AUDIT_MAX_BYTES:
            return
    except OSError:
        return
    backup = AUDIT_LOG.with_suffix(".log.1")
    try:
        backup.unlink(missing_ok=True)
        AUDIT_LOG.rename(backup)
    except Exception as exc:
        logger.warning("Audit log rotation failed: %s", exc)


def audit(chat_id: int, text: str) -> None:
    ts = datetime.now(timezone.utc).isoformat()
    _rotate_audit_if_needed()
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] chat={chat_id} | {text[:500]}\n")
    except Exception:
        pass
