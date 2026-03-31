"""MCP Webhook Channel Server – Remote Control bridge.

This module runs as a subprocess spawned by Claude Code when the bot operates
in ``claude-channel`` engine mode.  It speaks the MCP JSON-RPC-over-stdio
protocol and simultaneously exposes a local HTTP server so the Python bot can
send prompts and receive replies.

Architecture
============
Python bot  ──HTTP POST /prompt──►  channel_server (this module)
                                          │
                                  MCP notifications/claude/channel
                                          │
                                          ▼
                                    Claude Code (persistent session)
                                          │
                                  MCP tools/call  reply(chat_id, text)
                                          │
                                          ▼
Python bot  ◄──HTTP 200 text response──  channel_server

Usage (invoked by Claude Code via .mcp.json)
============================================
    uv run python -m companion.core.channel_server

Environment variables
=====================
CHANNEL_PORT  – HTTP port (default 8789)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import threading

logger = logging.getLogger(__name__)

CHANNEL_PORT = int(os.environ.get("CHANNEL_PORT", "8789"))
CHANNEL_HOST = os.environ.get("CHANNEL_HOST", "127.0.0.1")
RESPONSE_TIMEOUT = float(os.environ.get("CHANNEL_TIMEOUT", "300"))

# ── Global state ────────────────────────────────────────────────────────────

_reply_queues: dict[str, asyncio.Queue] = {}
_loop: asyncio.AbstractEventLoop | None = None
_stdin_queue: asyncio.Queue = asyncio.Queue()

# ── JSON-RPC / MCP helpers ───────────────────────────────────────────────────


def _write(data: dict) -> None:
    """Write one JSON-RPC message to stdout (Claude Code reads this)."""
    line = json.dumps(data, ensure_ascii=False) + "\n"
    sys.stdout.write(line)
    sys.stdout.flush()


def _response(msg_id, result: dict) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _error(msg_id, code: int, message: str) -> None:
    _write({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def _notification(method: str, params: dict) -> None:
    _write({"jsonrpc": "2.0", "method": method, "params": params})


# ── MCP message processing ───────────────────────────────────────────────────


async def _handle_mcp_message(raw: str) -> None:
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return

    method = msg.get("method", "")
    msg_id = msg.get("id")

    if method == "initialize":
        _response(msg_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {
                "tools": {},
                "experimental": {"claude/channel": {}},
            },
            "serverInfo": {"name": "channel", "version": "1.0.0"},
        })
        # Send initialized notification back
        _notification("notifications/initialized", {})

    elif method == "notifications/initialized":
        pass  # Claude Code acknowledges – nothing to do

    elif method == "tools/list":
        _response(msg_id, {
            "tools": [
                {
                    "name": "reply",
                    "description": (
                        "Send a response back to the user who triggered this session. "
                        "Call this once (or multiple times for streaming) with the answer. "
                        "The chat_id comes from the <channel> tag meta attribute."
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "chat_id": {
                                "type": "string",
                                "description": "The chat_id from the channel meta attribute",
                            },
                            "text": {
                                "type": "string",
                                "description": "Response text to send back to the user",
                            },
                        },
                        "required": ["chat_id", "text"],
                    },
                }
            ]
        })

    elif method == "tools/call":
        params = msg.get("params", {})
        tool_name = params.get("name")
        arguments = params.get("arguments", {})

        if tool_name == "reply":
            chat_id = str(arguments.get("chat_id", ""))
            text = str(arguments.get("text", ""))
            q = _reply_queues.get(chat_id)
            if q is not None:
                await q.put(text)
            _response(msg_id, {"content": [{"type": "text", "text": "ok"}]})
        else:
            _error(msg_id, -32601, f"Unknown tool: {tool_name}")

    # Ignore other methods (ping, etc.)


async def _mcp_reader_loop() -> None:
    """Process messages placed in _stdin_queue by the stdin thread."""
    while True:
        raw: str = await _stdin_queue.get()
        await _handle_mcp_message(raw)


# ── Stdin reader thread ───────────────────────────────────────────────────────


def _stdin_thread() -> None:
    """Blocking stdin reader; routes lines to the asyncio queue."""
    while True:
        try:
            line = sys.stdin.readline()
        except Exception:
            break
        if not line:
            break
        line = line.strip()
        if line and _loop is not None:
            asyncio.run_coroutine_threadsafe(_stdin_queue.put(line), _loop)


# ── HTTP server ───────────────────────────────────────────────────────────────

try:
    from aiohttp import web as _web
    _AIOHTTP = True
except ImportError:
    _AIOHTTP = False
    _web = None  # type: ignore


async def _send_channel_event(chat_id: str, prompt: str, cwd: str, platform: str) -> None:
    """Push a channel notification into the active Claude Code session."""
    _notification(
        "notifications/claude/channel",
        {
            "content": prompt,
            "meta": {
                "chat_id": chat_id,
                "cwd": cwd,
                "platform": platform,
            },
        },
    )


async def _http_handler_prompt(request) -> object:
    try:
        data = await request.json()
    except Exception:
        return _web.Response(status=400, text="Invalid JSON")  # type: ignore[union-attr]

    chat_id = str(data.get("chat_id", "0"))
    prompt = str(data.get("prompt", "")).strip()
    cwd = str(data.get("cwd", "."))
    platform = str(data.get("platform", "unknown"))

    if not prompt:
        return _web.Response(status=400, text="Empty prompt")  # type: ignore[union-attr]

    # Register reply queue before sending event (avoid race)
    q: asyncio.Queue = asyncio.Queue()
    _reply_queues[chat_id] = q

    parts: list[str] = []
    try:
        await _send_channel_event(chat_id, prompt, cwd, platform)
        deadline = asyncio.get_running_loop().time() + RESPONSE_TIMEOUT
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                break
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=min(remaining, 10.0))
                parts.append(chunk)
            except asyncio.TimeoutError:
                # No new chunk – if Claude hasn't replied yet check timeout
                if asyncio.get_running_loop().time() >= deadline:
                    break
                # If we already got something, assume Claude is done
                if parts:
                    break
    finally:
        _reply_queues.pop(chat_id, None)

    if not parts:
        return _web.Response(status=408, text="Response timeout")  # type: ignore[union-attr]

    full_text = "\n\n".join(parts)
    return _web.Response(text=full_text, content_type="text/plain")  # type: ignore[union-attr]


async def _http_handler_health(request) -> object:
    return _web.Response(text="ok")  # type: ignore[union-attr]


async def _start_http_server() -> None:
    if not _AIOHTTP:
        logger.error(
            "aiohttp not installed – HTTP interface unavailable. "
            "Add aiohttp to dependencies to enable claude-channel mode."
        )
        return
    app = _web.Application()  # type: ignore[union-attr]
    app.router.add_post("/prompt", _http_handler_prompt)
    app.router.add_get("/health", _http_handler_health)
    runner = _web.AppRunner(app)  # type: ignore[union-attr]
    await runner.setup()
    site = _web.TCPSite(runner, CHANNEL_HOST, CHANNEL_PORT)  # type: ignore[union-attr]
    await site.start()
    logger.info("Channel HTTP server listening on %s:%s", CHANNEL_HOST, CHANNEL_PORT)


# ── Entry point ───────────────────────────────────────────────────────────────


async def _amain() -> None:
    global _loop
    _loop = asyncio.get_running_loop()

    # Start stdin reader thread
    t = threading.Thread(target=_stdin_thread, daemon=True)
    t.start()

    # Start MCP message processor
    asyncio.create_task(_mcp_reader_loop())

    # Start HTTP server
    await _start_http_server()

    # Block forever (Claude Code will terminate this process when done)
    await asyncio.get_event_loop().create_future()


def main() -> None:
    logging.basicConfig(
        level=logging.WARNING,
        stream=sys.stderr,
        format="%(levelname)s [channel_server] %(message)s",
    )
    try:
        asyncio.run(_amain())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
