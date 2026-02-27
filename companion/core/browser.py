"""Interactive folder browser helpers."""

from __future__ import annotations

from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from companion.core.config import BROWSE_PAGE_SIZE
from companion.core.state import get_state


def list_subdirs(path: str) -> list[str]:
    try:
        return sorted(
            [entry.name for entry in Path(path).iterdir() if entry.is_dir()],
            key=lambda x: x.lower(),
        )
    except Exception:
        return []


def start_browser(chat_id: int, start_path: str) -> None:
    state = get_state(chat_id)
    resolved = str(Path(start_path).resolve())
    state["browse_root"] = resolved
    state["browse_path"] = resolved
    state["browse_page"] = 0


def build_browser_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    state = get_state(chat_id)
    root = state.get("browse_root") or state["cwd"]
    current = state.get("browse_path") or root
    page = int(state.get("browse_page", 0))

    subdirs = list_subdirs(current)
    total = len(subdirs)
    total_pages = max(1, (total + BROWSE_PAGE_SIZE - 1) // BROWSE_PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    state["browse_page"] = page

    start = page * BROWSE_PAGE_SIZE
    end = min(start + BROWSE_PAGE_SIZE, total)
    rows: list[list[InlineKeyboardButton]] = []

    for idx in range(start, end):
        rows.append(
            [InlineKeyboardButton(f"[DIR] {subdirs[idx]}", callback_data=f"browse:open:{idx}")]
        )

    nav_row: list[InlineKeyboardButton] = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("<<", callback_data="browse:page:prev"))
    if end < total:
        nav_row.append(InlineKeyboardButton(">>", callback_data="browse:page:next"))
    if nav_row:
        rows.append(nav_row)

    action_row: list[InlineKeyboardButton] = []
    if Path(current) != Path(root):
        action_row.append(InlineKeyboardButton("Up", callback_data="browse:up"))
    action_row.append(InlineKeyboardButton("Use this folder", callback_data="browse:use"))
    rows.append(action_row)
    rows.append([InlineKeyboardButton("Refresh", callback_data="browse:refresh")])

    text = (
        "Folder browser\n\n"
        f"Root: {root}\n"
        f"Current: {current}\n"
        f"Page: {page + 1}/{total_pages} ({total} folders)\n\n"
        "Tap a folder to go deeper, then tap 'Use this folder'."
    )
    return text, InlineKeyboardMarkup(rows)

