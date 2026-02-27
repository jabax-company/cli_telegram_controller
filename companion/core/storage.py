"""Filesystem persistence helpers."""

from __future__ import annotations

import json

from companion.core.config import AUDIT_LOG, PROJECTS_FILE


def load_projects() -> dict[str, str]:
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_projects(projects: dict[str, str]) -> None:
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


def audit(chat_id: int, text: str) -> None:
    from datetime import datetime, timezone

    ts = datetime.now(timezone.utc).isoformat()
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] chat={chat_id} | {text[:500]}\n")
    except Exception:
        pass

