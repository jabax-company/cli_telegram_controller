"""Text sanitization and safety checks."""

from __future__ import annotations

import re

from companion.core.config import EXTRA_BLOCKED

RAW_BLOCKLIST = [
    r"rm\s+-[rf]{1,2}\s+[/~]",
    r"rm\s+-[rf]{1,2}\s+\*",
    r"(?im)(?:^|[;&|]\s*|\n\s*)sudo\s+rm\b",
    r"(?im)(?:^|[;&|]\s*|\n\s*)remove-item\b[^\n]*-(recurse|force)",
    r"\bgit\s+reset\s+--hard\b",
    r"\bgit\s+clean\s+-[^\n]*f",
    r"\bdd\s+if=/dev/",
    r"\bmkfs\b",
    r":\(\)\s*\{.*\}",
    r">\s*/dev/sd[a-z]",
    r"chmod\s+-[Rr]\s+777\s+/",
    r"(wget|curl)\s+[^\s]+\s*\|\s*(ba)?sh",
    r"python\d*\s+[^\|]+\|\s*(ba)?sh",
] + [re.escape(p) for p in EXTRA_BLOCKED]

BLOCKLIST: list[re.Pattern[str]] = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in RAW_BLOCKLIST
]

ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]"
    r"|\x1b\][^\x07]*\x07"
    r"|\x1b[()][AB012]"
    r"|\r"
    r"|\x1b=|\x1b>"
    r"|\x1b[78]"
    r"|\x1b\[[?][0-9;]*[lh]"
)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def is_blocked(text: str) -> bool:
    return blocked_match(text) is not None


def blocked_match(text: str) -> str | None:
    for pattern in BLOCKLIST:
        found = pattern.search(text)
        if not found:
            continue
        snippet = (found.group(0) or "").strip()
        if len(snippet) > 120:
            snippet = snippet[:120] + "..."
        return snippet or pattern.pattern
    return None
