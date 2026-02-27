"""Interaction tracking and inactivity shutdown helpers."""

from __future__ import annotations

import asyncio
import logging
import time

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.config import INACTIVITY_TIMEOUT_SECS
from companion.core.server_runtime import stop_serve
from companion.core.state import (
    get_serve_state,
    get_state,
    is_serve_active,
    known_chat_ids,
    touch_activity,
)

logger = logging.getLogger(__name__)


async def track_activity_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not isinstance(update, Update):
        return
    if not is_authorized(update):
        return
    chat = update.effective_chat
    if chat is None:
        return
    touch_activity(chat.id)


async def stop_claude_session(state: dict) -> bool:
    proc = state.get("proc")
    was_active = bool(state.get("session_active")) or bool(proc and proc.returncode is None)

    if proc and proc.returncode is None:
        try:
            proc.terminate()
        except Exception:
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass

    for name in ("output_task", "keepalive_task"):
        task = state.get(name)
        if task and not task.done():
            task.cancel()
        state[name] = None

    state["session_active"] = False
    state["proc"] = None
    return was_active


async def _run_inactivity_watchdog(bot) -> None:
    now = time.monotonic()
    timeout_minutes = max(1, INACTIVITY_TIMEOUT_SECS // 60)

    for chat_id in known_chat_ids():
        state = get_state(chat_id)
        last_interaction = float(state.get("last_interaction", now))
        if (now - last_interaction) < INACTIVITY_TIMEOUT_SECS:
            continue

        stopped_claude = await stop_claude_session(state)
        serve_state = get_serve_state(chat_id)
        serve_active = bool(
            is_serve_active(chat_id)
            or serve_state.get("app_proc")
            or (serve_state.get("extra_procs") or [])
        )
        stopped_server = False
        if serve_active:
            await stop_serve(chat_id)
            stopped_server = True

        if not stopped_claude and not stopped_server:
            state["last_interaction"] = now
            continue

        state["claude_mode"] = False
        state["last_interaction"] = now
        stopped_parts: list[str] = []
        if stopped_claude:
            stopped_parts.append("Claude session")
        if stopped_server:
            stopped_parts.append("server")
        stopped_text = " and ".join(stopped_parts) if stopped_parts else "active resources"
        try:
            await bot.send_message(
                chat_id,
                f"No interaction for {timeout_minutes} minutes. "
                f"{stopped_text} stopped automatically.",
            )
        except Exception as exc:
            logger.warning("Could not send inactivity notice to chat %s: %s", chat_id, exc)


async def inactivity_watchdog(context: ContextTypes.DEFAULT_TYPE) -> None:
    await _run_inactivity_watchdog(context.bot)


async def inactivity_watchdog_once(bot) -> None:
    await _run_inactivity_watchdog(bot)
