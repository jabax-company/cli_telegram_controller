"""Channel-based Claude Code Runtime – Remote Control mode.

When AI_ENGINE (or per-chat ai_engine) is set to "claude-channel", the bot
starts a **persistent** Claude Code session with a local MCP webhook channel
and communicates with it via HTTP instead of spawning a new subprocess per
message.

How it works
============
1. ``ensure_channel_running()`` starts Claude Code once with:
       claude --dangerously-load-development-channels server:channel
   where ``channel`` is the MCP server defined in ``.mcp.json``.
2. Claude Code spawns companion.core.channel_server, which also starts an
   HTTP server on localhost:CHANNEL_PORT.
3. ``run_task_channel()`` POSTs the prompt to that HTTP server, waits for
   Claude to call the ``reply`` MCP tool, then delivers the answer via the
   SendAdapter.
"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from companion.core.config import (
    BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT,
    DISALLOWED_BASH_TOOLS,
    ENFORCE_BACKEND_RUNBOOK,
    RUN_GUIDE_APPEND_SYSTEM_PROMPT,
    SAFE_APPEND_SYSTEM_PROMPT,
    SAFE_MODE,
)
from companion.core.send_adapter import SendAdapter
from companion.core.state import get_state

logger = logging.getLogger(__name__)

_channel_proc: asyncio.subprocess.Process | None = None

_MAX_CHUNK = 3800


def _get_channel_url() -> str:
    # Lazy import so that CHANNEL_HOST/PORT are read after config is loaded
    from companion.core.config import CHANNEL_HOST, CHANNEL_PORT  # noqa: PLC0415
    return f"http://{CHANNEL_HOST}:{CHANNEL_PORT}"


async def _is_server_up() -> bool:
    try:
        import aiohttp  # type: ignore[import-untyped]
        url = _get_channel_url()
        timeout = aiohttp.ClientTimeout(total=2)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{url}/health") as r:
                return r.status == 200
    except Exception:
        return False


async def ensure_channel_running() -> bool:
    """Start the persistent Claude Code channel process if not running.

    Returns True if ready, False on failure.
    """
    global _channel_proc

    # Still alive?
    if _channel_proc is not None and _channel_proc.returncode is None:
        if await _is_server_up():
            return True
        # Give HTTP server a moment to start
        for _ in range(10):
            await asyncio.sleep(1)
            if await _is_server_up():
                return True
        logger.warning("Channel process alive but HTTP not responding – restarting.")
        try:
            _channel_proc.terminate()
        except Exception:
            pass
        _channel_proc = None

    logger.info("Starting Claude Code in channel mode (Remote Control).")
    append_prompts: list[str] = [RUN_GUIDE_APPEND_SYSTEM_PROMPT]
    if ENFORCE_BACKEND_RUNBOOK:
        append_prompts.append(BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT)
    if SAFE_MODE:
        append_prompts.append(SAFE_APPEND_SYSTEM_PROMPT)

    cmd = [
        "claude",
        "--dangerously-load-development-channels",
        "server:channel",
        "--dangerously-skip-permissions",
    ]
    if SAFE_MODE:
        cmd += ["--disallowedTools", *DISALLOWED_BASH_TOOLS]
    if append_prompts:
        cmd += ["--append-system-prompt", "\n\n".join(append_prompts)]

    try:
        _channel_proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
    except FileNotFoundError:
        logger.error("'claude' not found – cannot start channel mode.")
        return False
    except Exception as exc:
        logger.error("Failed to start Claude Code channel: %s", exc)
        return False

    # Wait up to 20 s for HTTP server to be ready
    for _ in range(20):
        await asyncio.sleep(1)
        if await _is_server_up():
            logger.info("Claude Code channel ready at %s", _get_channel_url())
            return True

    logger.error("Claude Code channel HTTP server did not come up in time.")
    return False


async def stop_channel() -> None:
    """Terminate the persistent Claude Code channel process."""
    global _channel_proc
    if _channel_proc is not None:
        try:
            _channel_proc.terminate()
        except Exception:
            pass
        _channel_proc = None


def _split_text(text: str) -> list[str]:
    chunks: list[str] = []
    while len(text) > _MAX_CHUNK:
        split_at = text.rfind("\n", 0, _MAX_CHUNK)
        if split_at < 200:
            split_at = _MAX_CHUNK
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    if text:
        chunks.append(text)
    return chunks


async def run_task_channel(
    chat_id: int,
    prompt: str,
    adapter: SendAdapter,
    platform: str = "unknown",
) -> None:
    """Send a prompt to the persistent Claude Code channel and stream reply."""
    state = get_state(chat_id)
    cwd = str(Path(state["cwd"]).resolve())
    state["cwd"] = cwd

    if state.get("session_active"):
        await adapter.send_text("Claude is working. Please wait.")
        return

    state["session_active"] = True
    try:
        ready = await ensure_channel_running()
        if not ready:
            await adapter.send_html(
                "<b>Error:</b> No se pudo iniciar el canal Remote Control.\n"
                "Asegúrate de que Claude Code está instalado, autenticado y de que "
                "<code>.mcp.json</code> está presente en la raíz del bot."
            )
            return

        try:
            import aiohttp  # type: ignore[import-untyped]
        except ImportError:
            await adapter.send_html(
                "<b>Error:</b> <code>aiohttp</code> no instalado.\n"
                "Ejecuta <code>uv sync</code>."
            )
            return

        url = _get_channel_url()
        await adapter.send_html(
            f"<b>Claude Code (Remote Control)</b> procesando...\n<code>{cwd}</code>"
        )

        timeout = aiohttp.ClientTimeout(total=300, connect=10)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                payload = {
                    "chat_id": str(chat_id),
                    "prompt": prompt,
                    "cwd": cwd,
                    "platform": platform,
                }
                async with session.post(f"{url}/prompt", json=payload) as resp:
                    if resp.status == 408:
                        await adapter.send_text("Tiempo de espera agotado sin respuesta de Claude.")
                        return
                    if resp.status != 200:
                        body = await resp.text()
                        await adapter.send_html(
                            f"<b>Error del canal ({resp.status}):</b>\n<code>{body[:500]}</code>"
                        )
                        return
                    full_text = await resp.text()
        except asyncio.TimeoutError:
            await adapter.send_text("Tiempo de espera agotado esperando respuesta.")
            return
        except Exception as exc:
            await adapter.send_html(
                f"<b>Error de conexión al canal:</b>\n<code>{exc}</code>"
            )
            return

        for chunk in _split_text(full_text):
            await adapter.send_text(chunk)

        has_session = bool(state.get("session_id"))
        hint = " Context kept. Use /reset to clear." if has_session else ""
        await adapter.send_text(f"Done.{hint}")
    finally:
        state["session_active"] = False
