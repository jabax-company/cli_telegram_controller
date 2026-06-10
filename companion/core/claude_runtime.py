"""Claude process orchestration and stream handling."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.config import (
    BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT,
    CLAUDE_SKIP_PERMISSIONS,
    DISALLOWED_BASH_TOOLS,
    ENFORCE_BACKEND_RUNBOOK,
    FLUSH_INTERVAL,
    FLUSH_SIZE,
    KEEPALIVE_SECS,
    MAX_MSG,
    MAX_OUTPUT_CHUNKS,
    RESTRICT_PATHS,
    RUN_GUIDE_APPEND_SYSTEM_PROMPT,
    SAFE_APPEND_SYSTEM_PROMPT,
    SAFE_MODE,
)
from companion.core.security import strip_ansi
from companion.core.state import get_state
from companion.core.storage import audit

logger = logging.getLogger(__name__)
_PATH_FLAG_CACHE: str | None = None
_PATH_FLAG_CHECKED = False


async def _detect_path_restriction_flag() -> str | None:
    global _PATH_FLAG_CACHE, _PATH_FLAG_CHECKED
    if _PATH_FLAG_CHECKED:
        return _PATH_FLAG_CACHE

    def _detect() -> str | None:
        try:
            completed = subprocess.run(
                ["claude", "--help"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                shell=False,
            )
            help_text = (completed.stdout or "") + "\n" + (completed.stderr or "")
        except Exception:
            return None

        if "--add-dir" in help_text:
            return "--add-dir"
        if "--allowedPaths" in help_text:
            return "--allowedPaths"
        return None

    _PATH_FLAG_CACHE = await asyncio.to_thread(_detect)
    _PATH_FLAG_CHECKED = True
    return _PATH_FLAG_CACHE


def split_message(text: str, max_len: int = MAX_MSG, max_chunks: int = MAX_OUTPUT_CHUNKS) -> list[str]:
    """Split long output into Telegram-sized pieces, preferring newline boundaries."""
    pieces: list[str] = []
    remaining = text
    while remaining and len(pieces) < max_chunks:
        if len(remaining) <= max_len:
            pieces.append(remaining)
            remaining = ""
            break
        cut = remaining.rfind("\n", max_len // 2, max_len)
        if cut == -1:
            cut = max_len
        pieces.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        pieces[-1] += f"\n\n(truncated {len(remaining)} more characters)"
    return pieces


async def send_chunk(bot, chat_id: int, text: str) -> None:
    clean = strip_ansi(text).strip()
    if not clean:
        return
    for piece in split_message(clean):
        try:
            await bot.send_message(chat_id, piece)
        except Exception as e:
            logger.warning("send_chunk error for chat %s: %s", chat_id, e)


async def queue_prompt_from_text(
    chat_id: int,
    text: str,
    message,
    source: str = "text",
) -> None:
    state = get_state(chat_id)
    state["pending_prompt"] = text
    audit(chat_id, f"PENDING_{source.upper()}: {text}")
    preview = text if len(text) <= 400 else text[:400] + "..."
    await message.reply_text(
        "Prompt saved.\n"
        "Send /claude to run it.\n\n"
        f"Saved from {source}: {preview}"
    )


async def output_reader(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = get_state(chat_id)
    proc: asyncio.subprocess.Process = state["proc"]
    bot = context.bot
    loop = asyncio.get_running_loop()
    logger.info("output_reader[%s]: started", chat_id)

    text_buf = ""
    line_buf = ""
    last_flush = loop.time()

    async def _flush() -> None:
        nonlocal text_buf, last_flush
        if text_buf.strip():
            await send_chunk(bot, chat_id, text_buf)
            text_buf = ""
        last_flush = loop.time()

    def _handle_line(raw: str) -> None:
        nonlocal text_buf
        raw = raw.strip()
        if not raw:
            return
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError:
            text_buf += raw + "\n"
            return

        obj_type = obj.get("type")
        if obj_type == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text_buf += block["text"]
        elif obj_type == "result":
            sid = obj.get("session_id")
            if sid:
                state["session_id"] = sid
                logger.info("output_reader[%s]: session_id=%s", chat_id, sid)

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=FLUSH_INTERVAL)
            except asyncio.TimeoutError:
                await _flush()
                if proc.returncode is not None:
                    break
                continue

            if not chunk:
                break

            decoded = chunk.decode("utf-8", errors="replace")
            line_buf += decoded

            while "\n" in line_buf:
                line, line_buf = line_buf.split("\n", 1)
                _handle_line(line)

            now = loop.time()
            if len(text_buf) >= FLUSH_SIZE or now - last_flush >= FLUSH_INTERVAL:
                await _flush()
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("output_reader[%s] error: %s", chat_id, e)
    finally:
        if line_buf.strip():
            _handle_line(line_buf)
        await _flush()
        state["session_active"] = False
        state["proc"] = None
        if state.get("keepalive_task") and not state["keepalive_task"].done():
            state["keepalive_task"].cancel()
        has_session = bool(state.get("session_id"))
        hint = " Next message keeps context. Use /reset for a fresh context." if has_session else ""
        try:
            await bot.send_message(chat_id, f"Done.{hint}")
        except Exception:
            pass


async def keepalive(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    status_msg = None
    elapsed = 0
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_SECS)
            elapsed += KEEPALIVE_SECS
            state = get_state(chat_id)
            if not state["session_active"]:
                return
            text = f"Still working... ({elapsed // 60}m {elapsed % 60:02d}s)"
            try:
                if status_msg is None:
                    status_msg = await context.bot.send_message(chat_id, text)
                else:
                    await status_msg.edit_text(text)
            except Exception:
                # Message may have been deleted or edit rejected; start a new one.
                status_msg = None
    except asyncio.CancelledError:
        pass


async def spawn_claude(
    chat_id: int, prompt: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd

    cmd = ["claude"]
    if CLAUDE_SKIP_PERMISSIONS:
        cmd.append("--dangerously-skip-permissions")
    else:
        # Headless runs cannot answer permission prompts; acceptEdits allows
        # file edits but still refuses tools outside the permission rules.
        cmd += ["--permission-mode", "acceptEdits"]
    cmd += [
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    append_prompts: list[str] = [RUN_GUIDE_APPEND_SYSTEM_PROMPT]
    if ENFORCE_BACKEND_RUNBOOK:
        append_prompts.append(BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT)
    if SAFE_MODE:
        append_prompts.append(SAFE_APPEND_SYSTEM_PROMPT)
        cmd += ["--disallowedTools", *DISALLOWED_BASH_TOOLS]
    if append_prompts:
        cmd += ["--append-system-prompt", "\n\n".join(append_prompts)]
    if state["session_id"]:
        cmd += ["--resume", state["session_id"]]
    if RESTRICT_PATHS:
        path_flag = await _detect_path_restriction_flag()
        if path_flag:
            cmd += [path_flag, cwd]
        else:
            logger.warning(
                "RESTRICT_PATHS=true but no supported path flag was detected in 'claude --help'. "
                "Running without path restriction flag."
            )

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    state["proc"] = proc
    state["session_active"] = True

    loop = asyncio.get_running_loop()
    state["output_task"] = loop.create_task(output_reader(chat_id, context))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, context))


async def run_task(
    chat_id: int,
    prompt: str,
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
) -> None:
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd
    msg = await update.effective_message.reply_text(f"Starting Claude Code in {cwd}...")
    try:
        await spawn_claude(chat_id, prompt, context)
        await msg.edit_text(
            f"Claude Code running in {cwd}.\n"
            "Output streams below. /stop to interrupt, /reset to kill."
        )
    except FileNotFoundError:
        await msg.edit_text(
            "Failed: `claude` command not found. Install Claude Code and ensure it is in PATH."
        )
    except Exception as e:
        await msg.edit_text(f"Failed to start Claude Code: {e}")
