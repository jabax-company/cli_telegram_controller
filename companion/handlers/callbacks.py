"""Callback query handlers."""

from __future__ import annotations

import html
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.browser import build_browser_view, list_subdirs, start_browser
from companion.core.config import AI_ENGINE
from companion.core.scheduler import list_tasks
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

    # ── Engine switch from /engine inline buttons ─────────────────────────
    if data.startswith("engine:"):
        new_engine = data[7:]
        if new_engine not in ("claude", "codex"):
            await query.answer("Motor no reconocido.")
            return
        if state.get("session_active"):
            await query.answer("Hay una sesión activa. Usa /stop primero.", show_alert=True)
            return
        state["ai_engine"] = new_engine
        if new_engine == "codex":
            state["session_id"] = None
            state["inject_resume_next"] = False
        label = "Claude Code" if new_engine == "claude" else "Codex"
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [[
            InlineKeyboardButton(
                "✅ Claude Code" if new_engine == "claude" else "Claude Code",
                callback_data="engine:claude",
            ),
            InlineKeyboardButton(
                "✅ Codex" if new_engine == "codex" else "Codex",
                callback_data="engine:codex",
            ),
        ]]
        await query.edit_message_text(
            f"<b>Motor cambiado a {label.upper()}</b>\n"
            "<i>Aplica a la próxima ejecución.</i>",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    # ── Quick actions from /start inline keyboard ─────────────────────────
    if data == "quick:paths":
        start_browser(chat_id, state["cwd"])
        text, markup = build_browser_view(chat_id)
        await query.edit_message_text(text, reply_markup=markup)
        return

    if data == "quick:projects":
        projects = load_projects()
        if not projects:
            await query.edit_message_text(
                "No hay proyectos guardados.\nUsa <code>/save &lt;nombre&gt;</code> para guardar el directorio actual.",
                parse_mode="HTML",
            )
            return
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        keyboard = [
            [InlineKeyboardButton(f"📁 {name}", callback_data=f"cd:{name}")]
            for name in projects
        ]
        lines = "\n".join(f"<code>{name}</code> → <code>{html.escape(path)}</code>" for name, path in projects.items())
        await query.edit_message_text(
            f"<b>Proyectos guardados:</b>\n\n{lines}",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    if data == "quick:status":
        engine = AI_ENGINE.upper()
        icon = "🟢 ACTIVO" if state["session_active"] else "⚪ INACTIVO"
        has_ctx = "sí" if state.get("session_id") else "no"
        pending = "sí" if state.get("pending_prompt") else "no"
        branch = state.get("branch_lock") or "—"
        await query.edit_message_text(
            f"<b>Estado del bot</b>  <i>[{engine}]</i>\n\n"
            f"<b>Sesión:</b> {icon}\n"
            f"<b>Dir actual:</b> <code>{html.escape(state['cwd'])}</code>\n"
            f"<b>Contexto resumible:</b> {has_ctx}\n"
            f"<b>Prompt pendiente:</b> {pending}\n"
            f"<b>Rama bloqueada:</b> <code>{html.escape(branch)}</code>",
            parse_mode="HTML",
        )
        return

    if data == "quick:scheduled":
        tasks = list_tasks(chat_id)
        if not tasks:
            await query.edit_message_text(
                "No hay tareas programadas.\nUsa <code>/at HH:MM &lt;prompt&gt;</code> para crear una.",
                parse_mode="HTML",
            )
            return
        from datetime import datetime
        lines = []
        for t in tasks:
            run_at = datetime.fromisoformat(t["run_at"])
            preview = t["prompt"][:60] + ("..." if len(t["prompt"]) > 60 else "")
            lines.append(
                f"<code>[{t['id']}]</code> {run_at.strftime('%d/%m %H:%M')} "
                f"({t['type']})\n  {html.escape(preview)}"
            )
        await query.edit_message_text(
            f"<b>Tareas programadas ({len(tasks)}):</b>\n\n" + "\n\n".join(lines) +
            "\n\n<i>/unschedule &lt;id&gt; para cancelar</i>",
            parse_mode="HTML",
        )
        return

