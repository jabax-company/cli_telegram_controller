"""Remote computer control command handlers."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.config import MAX_DOWNLOAD_MB
from companion.core.state import get_state
from companion.core.storage import audit
from companion.core.system import (
    describe_process,
    get_system_info,
    kill_process,
    list_processes,
    lock_screen,
    take_screenshot,
)


async def cmd_sysinfo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    try:
        info = await asyncio.to_thread(get_system_info)
    except Exception as e:
        await update.effective_message.reply_text(f"Could not read system info: {e}")
        return
    await update.effective_message.reply_text(info)


async def cmd_screenshot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    audit(chat_id, "SCREENSHOT")
    msg = await update.effective_message.reply_text("Capturing screen...")
    path = None
    try:
        path = await asyncio.to_thread(take_screenshot)
        with open(path, "rb") as fh:
            await update.effective_message.reply_document(fh, filename="screenshot.png")
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Screenshot failed: {e}")
    finally:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass


async def cmd_ps(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    name_filter = " ".join(context.args) if context.args else ""
    try:
        listing = await asyncio.to_thread(list_processes, name_filter)
    except Exception as e:
        await update.effective_message.reply_text(f"Could not list processes: {e}")
        return
    await update.effective_message.reply_text(listing)


async def cmd_kill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    args = context.args or []
    if not args or not args[0].isdigit():
        await update.effective_message.reply_text("Usage: /kill <pid> [yes]")
        return
    pid = int(args[0])
    confirmed = len(args) > 1 and args[1].lower() == "yes"

    if not confirmed:
        try:
            desc = await asyncio.to_thread(describe_process, pid)
        except Exception as e:
            await update.effective_message.reply_text(f"Process {pid} not found: {e}")
            return
        await update.effective_message.reply_text(
            f"Target: {desc}\nSend /kill {pid} yes to terminate it."
        )
        return

    try:
        result = await asyncio.to_thread(kill_process, pid)
    except Exception as e:
        await update.effective_message.reply_text(f"Could not kill {pid}: {e}")
        return
    audit(chat_id, f"KILL: pid={pid}")
    await update.effective_message.reply_text(result)


async def cmd_lock(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    try:
        result = await asyncio.to_thread(lock_screen)
    except Exception as e:
        await update.effective_message.reply_text(f"Could not lock screen: {e}")
        return
    audit(chat_id, "LOCK_SCREEN")
    await update.effective_message.reply_text(result)


def resolve_download_path(arg: str, cwd: str) -> Path | None:
    """Resolve a user-supplied file path: absolute, ~, or relative to cwd."""
    expanded = os.path.expanduser(arg)
    candidate = Path(expanded)
    if not candidate.is_absolute():
        candidate = Path(cwd) / expanded
    candidate = candidate.resolve()
    return candidate if candidate.is_file() else None


async def cmd_download(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    if not context.args:
        await update.effective_message.reply_text("Usage: /download <file-path>")
        return

    arg = " ".join(context.args)
    target = resolve_download_path(arg, state["cwd"])
    if target is None:
        await update.effective_message.reply_text(f"File not found: {arg}")
        return

    size_mb = target.stat().st_size / (1024 * 1024)
    if size_mb > MAX_DOWNLOAD_MB:
        await update.effective_message.reply_text(
            f"File is {size_mb:.1f} MB; limit is {MAX_DOWNLOAD_MB} MB."
        )
        return

    audit(chat_id, f"DOWNLOAD: {target}")
    msg = await update.effective_message.reply_text(f"Sending {target.name} ({size_mb:.1f} MB)...")
    try:
        with open(target, "rb") as fh:
            await update.effective_message.reply_document(fh, filename=target.name)
        await msg.delete()
    except Exception as e:
        await msg.edit_text(f"Could not send file: {e}")


__all__ = [
    "cmd_sysinfo",
    "cmd_screenshot",
    "cmd_ps",
    "cmd_kill",
    "cmd_lock",
    "cmd_download",
    "resolve_download_path",
]
