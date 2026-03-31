"""Claude / Codex process orchestration and stream handling."""

from __future__ import annotations

import asyncio
import json
import logging
import subprocess
from pathlib import Path

import html as html_mod

from companion.core.config import (
    AI_ENGINE,
    BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT,
    CODEX_MODEL,
    DISALLOWED_BASH_TOOLS,
    ENFORCE_BACKEND_RUNBOOK,
    FLUSH_INTERVAL,
    FLUSH_SIZE,
    KEEPALIVE_SECS,
    MAX_MSG,
    RESTRICT_PATHS,
    RUN_GUIDE_APPEND_SYSTEM_PROMPT,
    SAFE_APPEND_SYSTEM_PROMPT,
    SAFE_MODE,
)
from companion.core.security import strip_ansi
from companion.core.send_adapter import SendAdapter
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


async def send_chunk(adapter: SendAdapter, text: str) -> None:
    clean = strip_ansi(text).strip()
    if not clean:
        return
    if len(clean) > MAX_MSG:
        clean = clean[:MAX_MSG] + "\n\n(truncated)"
    await adapter.send_text(clean)


async def queue_prompt_from_text(
    chat_id: int,
    text: str,
    reply_fn,
    source: str = "text",
) -> None:
    """Save a prompt as pending and notify via reply_fn(text)."""
    state = get_state(chat_id)
    state["pending_prompt"] = text
    audit(chat_id, f"PENDING_{source.upper()}: {text}")
    preview = text if len(text) <= 400 else text[:400] + "..."
    await reply_fn(
        "Prompt saved.\n"
        "Send /claude to run it.\n\n"
        f"Saved from {source}: {preview}"
    )


async def output_reader(chat_id: int, adapter: SendAdapter) -> None:
    state = get_state(chat_id)
    proc: asyncio.subprocess.Process = state["proc"]
    loop = asyncio.get_running_loop()
    logger.info("output_reader[%s]: started", chat_id)

    text_buf = ""
    line_buf = ""
    last_flush = loop.time()

    async def _flush() -> None:
        nonlocal text_buf, last_flush
        if text_buf.strip():
            await send_chunk(adapter, text_buf)
            text_buf = ""
        last_flush = loop.time()

    output_format = state.get("output_format", "json")

    def _handle_line(raw: str) -> None:
        nonlocal text_buf
        raw = raw.strip()
        if not raw:
            return

        if output_format == "plain":
            text_buf += raw + "\n"
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

    stdout = proc.stdout
    assert stdout is not None
    try:
        while True:
            try:
                chunk = await asyncio.wait_for(stdout.read(4096), timeout=FLUSH_INTERVAL)
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
            await adapter.send_text(f"Done.{hint}")
        except Exception:
            pass


async def keepalive(chat_id: int, adapter: SendAdapter) -> None:
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_SECS)
            state = get_state(chat_id)
            if not state["session_active"]:
                return
            try:
                await adapter.send_text("Still working...")
            except Exception as exc:
                logger.debug("keepalive[%s] send failed: %s", chat_id, exc)
    except asyncio.CancelledError:
        pass


async def spawn_claude(
    chat_id: int, prompt: str, adapter: SendAdapter
) -> None:
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd
    state["output_format"] = "json"

    cmd = [
        "claude",
        "--dangerously-skip-permissions",
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
    state["output_task"] = loop.create_task(output_reader(chat_id, adapter))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, adapter))


async def spawn_codex(
    chat_id: int, prompt: str, adapter: SendAdapter
) -> None:
    """Spawn an OpenAI Codex CLI process."""
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd
    state["output_format"] = "plain"
    # Codex sessions are not resumable — clear any stale session_id
    state["session_id"] = None
    state["inject_resume_next"] = False

    # Model: per-chat override first, then global CODEX_MODEL
    model = state.get("ai_model") or CODEX_MODEL
    cmd = ["codex", "--approval-mode", "full-auto", "-q"]
    if model:
        cmd += ["--model", model]
    cmd.append(prompt)

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
    state["output_task"] = loop.create_task(output_reader(chat_id, adapter))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, adapter))


async def run_task(
    chat_id: int,
    prompt: str,
    adapter: SendAdapter,
    reply_fn=None,
    platform: str = "unknown",
) -> None:
    """Launch Claude, Codex, or the Remote Control channel for the given chat.

    adapter   – SendAdapter for streaming output chunks to the user.
    reply_fn  – optional async callable(html_text) for the initial status
                message.  When None, adapter.send_html is used.
    platform  – "telegram" or "discord" (for channel mode metadata).
    """
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd

    engine = state.get("ai_engine") or AI_ENGINE

    _notify = reply_fn if reply_fn is not None else adapter.send_html

    # ── Remote Control / channel mode ──────────────────────────────────────
    if engine == "claude-channel":
        from companion.core.channel_runtime import run_task_channel  # noqa: PLC0415
        await run_task_channel(chat_id, prompt, adapter, platform=platform)
        return

    # ── Subprocess mode (claude / codex) ───────────────────────────────────
    engine_label = "Codex" if engine == "codex" else "Claude Code"

    await _notify(
        f"<b>Iniciando {engine_label}</b>\n<code>{html_mod.escape(cwd)}</code>"
    )
    try:
        if engine == "codex":
            await spawn_codex(chat_id, prompt, adapter)
        else:
            await spawn_claude(chat_id, prompt, adapter)
        await _notify(
            f"<b>{engine_label} activo</b> en <code>{html_mod.escape(cwd)}</code>\n"
            "<i>/stop para interrumpir · /reset para reiniciar</i>"
        )
    except FileNotFoundError:
        binary = "codex" if engine == "codex" else "claude"
        await _notify(
            f"<b>Error:</b> comando <code>{binary}</code> no encontrado.\n"
            "Instala el CLI y asegúrate de que está en el PATH."
        )
    except Exception as e:
        await _notify(
            f"<b>Error al iniciar {engine_label}:</b>\n<code>{html_mod.escape(str(e))}</code>"
        )
