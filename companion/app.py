"""Application bootstrap."""

from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from companion.core.activity import inactivity_watchdog_once, stop_claude_session, track_activity_update
from companion.core.config import (
    INACTIVITY_CHECK_SECS,
    TELEGRAM_TOKEN,
    TELEGRAM_USER_ID,
    TRAY_ICON_ENABLED,
)
from companion.core.runtime_control import clear_stop_callback, register_stop_callback
from companion.core.server_runtime import cmd_server
from companion.core.server_runtime import stop_serve
from companion.core.state import get_state, known_chat_ids
from companion.core.tray_icon import start_tray_icon
from companion.handlers.callbacks import handle_callback
from companion.handlers.commands import (
    cmd_bot,
    cmd_base,
    cmd_branch,
    cmd_bash,
    cmd_cd,
    cmd_claude,
    cmd_exit,
    cmd_help,
    cmd_mkdir,
    cmd_paths,
    cmd_plan,
    cmd_projects,
    cmd_reset,
    cmd_save,
    cmd_start,
    cmd_status,
    cmd_stop,
)
from companion.handlers.messages import (
    handle_audio,
    handle_command_passthrough,
    handle_image,
    handle_message,
)

logger = logging.getLogger(__name__)


async def _watchdog_loop(app: Application) -> None:
    try:
        while True:
            await asyncio.sleep(INACTIVITY_CHECK_SECS)
            await inactivity_watchdog_once(app.bot)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Inactivity watchdog loop crashed: %s", exc)


async def _post_init(app: Application) -> None:
    task = asyncio.create_task(_watchdog_loop(app), name="inactivity_watchdog")
    app.bot_data["inactivity_watchdog_task"] = task


async def _post_shutdown(app: Application) -> None:
    task = app.bot_data.pop("inactivity_watchdog_task", None)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    for chat_id in known_chat_ids():
        state = get_state(chat_id)
        with contextlib.suppress(Exception):
            await stop_claude_session(state)
        with contextlib.suppress(Exception):
            await stop_serve(chat_id)


def main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .post_init(_post_init)
        .post_shutdown(_post_shutdown)
        .build()
    )

    def _external_stop(reason: str) -> None:
        logger.info("Stop requested (%s).", reason)
        if loop.is_closed():
            return
        loop.call_soon_threadsafe(app.stop_running)

    register_stop_callback(_external_stop)
    tray = start_tray_icon() if TRAY_ICON_ENABLED else None
    app.add_handler(TypeHandler(Update, track_activity_update), group=-1)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("bot", cmd_bot))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("3d", cmd_cd))
    app.add_handler(CommandHandler("base", cmd_base))
    app.add_handler(CommandHandler("mkdir", cmd_mkdir))
    app.add_handler(CommandHandler("paths", cmd_paths))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("branch", cmd_branch))
    app.add_handler(CommandHandler("claude", cmd_claude))
    app.add_handler(CommandHandler("exit", cmd_exit))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("bash", cmd_bash))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CommandHandler("server", cmd_server))
    app.add_handler(CommandHandler("serve", cmd_server))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.COMMAND, handle_command_passthrough))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, handle_image))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(
        "Claude Code Companion (local-mode) started. Authorized user: %s",
        TELEGRAM_USER_ID,
    )
    try:
        app.run_polling(
            drop_pending_updates=True,
            allowed_updates=["message", "callback_query"],
        )
    except KeyboardInterrupt:
        logger.info("Ctrl+C received. Stopping bot...")
    finally:
        clear_stop_callback()
        if tray is not None:
            tray.stop()
