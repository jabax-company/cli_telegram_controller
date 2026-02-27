"""Callback query handlers."""

from __future__ import annotations

import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.browser import build_browser_view, list_subdirs
from companion.core.state import get_state
from companion.core.storage import load_projects


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    query = update.callback_query
    if query is None:
        return
    await query.answer()
    if not is_authorized(update):
        return

    chat = update.effective_chat
    if chat is None:
        return
    chat_id = chat.id
    state = get_state(chat_id)
    data = query.data or ""

    if data.startswith("cd:"):
        name = data[3:]
        projects = load_projects()
        path = projects.get(name)
        if not path:
            await query.edit_message_text(f"Project not found: {name}")
            return
        if not os.path.isdir(path):
            await query.edit_message_text(f"Directory not found: {path}")
            return
        state["cwd"] = str(Path(path).resolve())
        await query.edit_message_text(f"Now in: {state['cwd']}")
        return

    if data.startswith("browse:"):
        root = state.get("browse_root")
        current = state.get("browse_path")
        if not root or not current:
            await query.edit_message_text("Folder browser expired. Use /paths again.")
            return

        if data == "browse:refresh":
            text, markup = build_browser_view(chat_id)
            await query.edit_message_text(text, reply_markup=markup)
            return

        if data == "browse:up":
            current_path = Path(current)
            root_path = Path(root)
            if current_path != root_path:
                parent = current_path.parent
                if str(parent).startswith(str(root_path)):
                    state["browse_path"] = str(parent)
                    state["browse_page"] = 0
            text, markup = build_browser_view(chat_id)
            await query.edit_message_text(text, reply_markup=markup)
            return

        if data == "browse:use":
            state["cwd"] = str(Path(current).resolve())
            await query.edit_message_text(f"Current directory set to:\n{state['cwd']}")
            return

        if data.startswith("browse:page:"):
            direction = data.split(":")[-1]
            page = int(state.get("browse_page", 0))
            if direction == "next":
                page += 1
            elif direction == "prev":
                page -= 1
            state["browse_page"] = page
            text, markup = build_browser_view(chat_id)
            await query.edit_message_text(text, reply_markup=markup)
            return

        if data.startswith("browse:open:"):
            try:
                idx = int(data.split(":")[-1])
            except Exception:
                await query.edit_message_text("Invalid selection.")
                return
            subdirs = list_subdirs(current)
            if idx < 0 or idx >= len(subdirs):
                await query.edit_message_text("Folder no longer available. Use /paths again.")
                return
            state["browse_path"] = str((Path(current) / subdirs[idx]).resolve())
            state["browse_page"] = 0
            text, markup = build_browser_view(chat_id)
            await query.edit_message_text(text, reply_markup=markup)
            return

