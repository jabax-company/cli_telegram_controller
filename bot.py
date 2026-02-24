"""Claude Code Companion — local-mode bot.

Telegram <-> Claude Code interactive PTY bridge.

You (Telegram) ──► Claude Code (interactive, on your machine) ──► your project
Claude Code output/questions ──► Telegram
Your replies ──► Claude Code stdin
"""

import asyncio
import json
import logging
import os
import re
import signal
import sys
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_USER_ID = int(os.environ["TELEGRAM_USER_ID"])
INITIAL_DIR = os.path.expanduser(os.environ.get("INITIAL_DIR", "~"))
RESTRICT_PATHS = os.environ.get("RESTRICT_PATHS", "false").lower() == "true"
_extra_blocked = os.environ.get("BLOCKED_PATTERNS", "")
EXTRA_BLOCKED = [p.strip() for p in _extra_blocked.split(",") if p.strip()]
IS_WINDOWS = sys.platform == "win32"

# ── Paths ─────────────────────────────────────────────────────────────────────
DATA_DIR = Path.home() / ".claude_code_bot"
PROJECTS_FILE = DATA_DIR / "projects.json"
AUDIT_LOG = DATA_DIR / "audit.log"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
for _noisy in ("httpx", "telegram", "telegram.ext.Application"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ── Blocklist ─────────────────────────────────────────────────────────────────
_RAW_BLOCKLIST = [
    r"rm\s+-[rf]{1,2}\s+[/~]",
    r"rm\s+-[rf]{1,2}\s+\*",
    r"sudo\s+rm",
    r"\bdd\s+if=/dev/",
    r"\bmkfs\b",
    r":\(\)\s*\{.*\}",               # fork bomb
    r">\s*/dev/sd[a-z]",
    r"chmod\s+-[Rr]\s+777\s+/",
    r"(wget|curl)\s+[^\s]+\s*\|\s*(ba)?sh",
    r"python\d*\s+[^\|]+\|\s*(ba)?sh",
] + [re.escape(p) for p in EXTRA_BLOCKED]

BLOCKLIST: list[re.Pattern] = [
    re.compile(p, re.IGNORECASE | re.DOTALL) for p in _RAW_BLOCKLIST
]

ANSI_RE = re.compile(
    r"\x1b\[[0-9;]*[a-zA-Z]"
    r"|\x1b\][^\x07]*\x07"
    r"|\x1b[()][AB012]"
    r"|\r"
    r"|\x1b=|\x1b>"
    r"|\x1b[78]"
    r"|\x1b\[[?][0-9;]*[lh]"
)

MAX_MSG = 3500          # Telegram message char limit with breathing room
KEEPALIVE_SECS = 30     # Send "still working" after this many idle seconds
FLUSH_INTERVAL = 2.0    # Seconds between output flushes to Telegram
FLUSH_SIZE = 500        # Bytes before forcing a flush


# ── Projects ──────────────────────────────────────────────────────────────────
def load_projects() -> dict[str, str]:
    try:
        return json.loads(PROJECTS_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_projects(projects: dict[str, str]) -> None:
    PROJECTS_FILE.write_text(json.dumps(projects, indent=2), encoding="utf-8")


# ── Session state (per chat_id) ───────────────────────────────────────────────
def _blank_state() -> dict:
    return {
        "cwd": INITIAL_DIR,
        "session_active": False,
        "proc": None,           # asyncio.subprocess.Process
        "master_fd": None,      # PTY master fd (Unix only)
        "output_task": None,    # asyncio.Task
        "keepalive_task": None, # asyncio.Task
        "pending_confirm": None,  # blocked prompt awaiting explicit YES
    }


_sessions: dict[int, dict] = {}


def get_state(chat_id: int) -> dict:
    if chat_id not in _sessions:
        _sessions[chat_id] = _blank_state()
    return _sessions[chat_id]


def is_authorized(update: Update) -> bool:
    return update.effective_user.id == TELEGRAM_USER_ID


# ── Helpers ───────────────────────────────────────────────────────────────────
def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def is_blocked(text: str) -> bool:
    return any(p.search(text) for p in BLOCKLIST)


def audit(chat_id: int, text: str) -> None:
    from datetime import datetime, timezone
    ts = datetime.now(timezone.utc).isoformat()
    try:
        with AUDIT_LOG.open("a", encoding="utf-8") as f:
            f.write(f"[{ts}] chat={chat_id} | {text[:500]}\n")
    except Exception:
        pass


async def send_chunk(bot, chat_id: int, text: str) -> None:
    """Send text as a Markdown code block, truncating if needed."""
    clean = strip_ansi(text).strip()
    if not clean:
        return
    if len(clean) > MAX_MSG:
        clean = clean[:MAX_MSG] + "\n\n_(output truncated — ask Claude to summarize)_"
    try:
        await bot.send_message(
            chat_id,
            f"```\n{clean}\n```",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("send_chunk error for chat %s: %s", chat_id, e)


# ── Output reader ─────────────────────────────────────────────────────────────
async def _output_reader(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stream Claude Code output to Telegram until the process exits."""
    s = get_state(chat_id)
    proc: asyncio.subprocess.Process = s["proc"]
    master_fd: int | None = s["master_fd"]
    bot = context.bot
    loop = asyncio.get_running_loop()

    buffer = ""
    last_flush = loop.time()

    async def _flush():
        nonlocal buffer, last_flush
        if buffer:
            await send_chunk(bot, chat_id, buffer)
            buffer = ""
        last_flush = loop.time()

    try:
        if master_fd is not None:
            # ── Unix PTY: thread reads master_fd, puts bytes in queue ──────
            queue: asyncio.Queue[bytes | None] = asyncio.Queue()

            def _pty_thread():
                import select as _sel
                try:
                    while True:
                        r, _, _ = _sel.select([master_fd], [], [], 1.0)
                        if r:
                            try:
                                data = os.read(master_fd, 4096)
                            except OSError:
                                break
                            if not data:
                                break
                            loop.call_soon_threadsafe(queue.put_nowait, data)
                        elif proc.returncode is not None:
                            break
                finally:
                    loop.call_soon_threadsafe(queue.put_nowait, None)

            threading.Thread(target=_pty_thread, daemon=True).start()

            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=FLUSH_INTERVAL)
                except asyncio.TimeoutError:
                    # Periodic flush
                    await _flush()
                    continue

                if item is None:
                    break
                buffer += item.decode("utf-8", errors="replace")

                now = loop.time()
                if len(buffer) >= FLUSH_SIZE or now - last_flush >= FLUSH_INTERVAL:
                    await _flush()

        else:
            # ── Pipe mode (Windows or PTY fallback) ───────────────────────
            while True:
                try:
                    data = await asyncio.wait_for(proc.stdout.read(4096), timeout=FLUSH_INTERVAL)
                    if not data:   # EOF
                        break
                    buffer += data.decode("utf-8", errors="replace")
                except asyncio.TimeoutError:
                    # No data in the flush window; check if process ended
                    if proc.returncode is not None:
                        break

                now = loop.time()
                if len(buffer) >= FLUSH_SIZE or now - last_flush >= FLUSH_INTERVAL:
                    await _flush()

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("output_reader error for chat %s: %s", chat_id, e)
    finally:
        await _flush()

        # Mark session as ended
        s["session_active"] = False
        s["proc"] = None

        if master_fd is not None:
            try:
                os.close(master_fd)
            except OSError:
                pass
            s["master_fd"] = None

        if s.get("keepalive_task") and not s["keepalive_task"].done():
            s["keepalive_task"].cancel()

        try:
            await bot.send_message(
                chat_id,
                "✅ Session ended. Send a new task or /cd to change directory.",
            )
        except Exception:
            pass


async def _keepalive(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodically remind the user Claude Code is still running."""
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_SECS)
            if not get_state(chat_id)["session_active"]:
                break
            try:
                await context.bot.send_message(chat_id, "⏳ Still working...")
            except Exception:
                pass
    except asyncio.CancelledError:
        pass


# ── Spawn Claude Code ─────────────────────────────────────────────────────────
async def spawn_claude(
    chat_id: int, prompt: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Spawn claude --dangerously-skip-permissions and wire it to Telegram."""
    s = get_state(chat_id)
    cwd = s["cwd"]

    cmd = ["claude", "--dangerously-skip-permissions"]
    if RESTRICT_PATHS:
        cmd += ["--allowedPaths", cwd]

    master_fd: int | None = None

    # ── Try PTY on Unix ────────────────────────────────────────────────────
    if not IS_WINDOWS:
        try:
            import pty
            master, slave = pty.openpty()
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=cwd,
                env={
                    **os.environ,
                    "TERM": "xterm-256color",
                    "COLUMNS": "160",
                    "LINES": "50",
                },
            )
            os.close(slave)
            master_fd = master
        except Exception as e:
            logger.warning("PTY spawn failed (%s) — falling back to pipes", e)
            master_fd = None

    # ── Pipe fallback (Windows or PTY failure) ─────────────────────────────
    if master_fd is None:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=cwd,
        )

    s["proc"] = proc
    s["master_fd"] = master_fd
    s["session_active"] = True

    # Send the initial prompt
    prompt_bytes = (prompt + "\n").encode()
    if master_fd is not None:
        os.write(master_fd, prompt_bytes)
    else:
        proc.stdin.write(prompt_bytes)
        await proc.stdin.drain()

    loop = asyncio.get_running_loop()
    s["output_task"] = loop.create_task(_output_reader(chat_id, context))
    s["keepalive_task"] = loop.create_task(_keepalive(chat_id, context))


# ── Relay user input to Claude Code stdin ────────────────────────────────────
async def relay_input(chat_id: int, text: str) -> None:
    s = get_state(chat_id)
    data = (text + "\n").encode()
    try:
        if s["master_fd"] is not None:
            os.write(s["master_fd"], data)
        elif s["proc"] and s["proc"].stdin:
            s["proc"].stdin.write(data)
            await s["proc"].stdin.drain()
    except OSError as e:
        logger.error("relay_input error: %s", e)


# ── Command handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    await update.message.reply_text(
        "*Claude Code Companion*\n\n"
        f"Directory: `{s['cwd']}`\n\n"
        "*Commands:*\n"
        "• `/cd <path|name>` — change working directory\n"
        "• `/projects` — list saved projects (tap to switch)\n"
        "• `/save <name>` — save current dir as a named project\n"
        "• `/status` — show current dir + session state\n"
        "• `/stop` — interrupt Claude Code (Ctrl+C)\n"
        "• `/reset` — kill session, start fresh\n\n"
        "Send any task description and Claude Code starts! 🚀",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    icon = "🟢 Active" if s["session_active"] else "⚫ Idle"
    await update.message.reply_text(
        f"*Status*\n"
        f"Directory: `{s['cwd']}`\n"
        f"Session: {icon}",
        parse_mode="Markdown",
    )


async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: `/cd <path or project name>`", parse_mode="Markdown"
        )
        return
    arg = " ".join(context.args)
    projects = load_projects()
    # Saved name takes priority; fall back to literal/expanded path
    target = projects.get(arg) or os.path.expanduser(arg)
    if not os.path.isdir(target):
        await update.message.reply_text(
            f"Directory not found: `{target}`", parse_mode="Markdown"
        )
        return
    get_state(update.effective_chat.id)["cwd"] = target
    await update.message.reply_text(f"Now in: `{target}`", parse_mode="Markdown")


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    projects = load_projects()
    if not projects:
        await update.message.reply_text(
            "No saved projects yet.\nUse `/save <name>` to save the current directory.",
            parse_mode="Markdown",
        )
        return
    keyboard = [
        [InlineKeyboardButton(f"📁 {name}", callback_data=f"cd:{name}")]
        for name in projects
    ]
    lines = "\n".join(f"• `{n}` → `{p}`" for n, p in projects.items())
    await update.message.reply_text(
        f"*Saved Projects:*\n{lines}",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/save <name>`", parse_mode="Markdown")
        return
    name = context.args[0]
    s = get_state(update.effective_chat.id)
    projects = load_projects()
    projects[name] = s["cwd"]
    save_projects(projects)
    await update.message.reply_text(
        f"Saved `{name}` → `{s['cwd']}`", parse_mode="Markdown"
    )


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    if not s["session_active"] or s["proc"] is None:
        await update.message.reply_text("No active session to stop.")
        return
    try:
        if s["master_fd"] is not None:
            os.write(s["master_fd"], b"\x03")  # Ctrl+C over PTY
        elif IS_WINDOWS:
            s["proc"].terminate()
        else:
            s["proc"].send_signal(signal.SIGINT)
        await update.message.reply_text("Sent interrupt to Claude Code.")
    except Exception as e:
        await update.message.reply_text(f"Error sending interrupt: {e}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    cwd = s["cwd"]  # preserve current directory

    if s["proc"]:
        try:
            s["proc"].kill()
        except Exception:
            pass
    for task in (s.get("output_task"), s.get("keepalive_task")):
        if task and not task.done():
            task.cancel()

    _sessions[update.effective_chat.id] = _blank_state()
    _sessions[update.effective_chat.id]["cwd"] = cwd
    await update.message.reply_text("Session reset. Ready for a new task.")


# ── Message handler ───────────────────────────────────────────────────────────
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    s = get_state(chat_id)
    text = (update.message.text or "").strip()
    if not text:
        return

    # ── Blocked-pattern confirmation pending ──────────────────────────────
    if s["pending_confirm"]:
        if text.strip().upper() == "YES":
            prompt = s["pending_confirm"]
            s["pending_confirm"] = None
            audit(chat_id, f"CONFIRMED_BLOCKED: {prompt}")
            await _run_task(chat_id, prompt, context, update)
        else:
            s["pending_confirm"] = None
            await update.message.reply_text("Cancelled.")
        return

    # ── Active session: relay input to Claude Code ────────────────────────
    if s["session_active"] and s["proc"]:
        await relay_input(chat_id, text)
        return

    # ── New task ──────────────────────────────────────────────────────────
    audit(chat_id, f"PROMPT: {text}")
    if is_blocked(text):
        s["pending_confirm"] = text
        await update.message.reply_text(
            "⚠️ *Blocked pattern detected*\n\n"
            "Your prompt matches a potentially destructive pattern.\n"
            "Reply `YES` to proceed anyway, or anything else to cancel.",
            parse_mode="Markdown",
        )
        return

    await _run_task(chat_id, text, context, update)


async def _run_task(
    chat_id: int,
    prompt: str,
    context: ContextTypes.DEFAULT_TYPE,
    update: Update,
) -> None:
    s = get_state(chat_id)
    msg = await update.message.reply_text(
        f"Starting Claude Code in `{s['cwd']}`...", parse_mode="Markdown"
    )
    try:
        await spawn_claude(chat_id, prompt, context)
        await msg.edit_text(
            f"Claude Code running in `{s['cwd']}`.\n"
            "Output streams below. `/stop` to interrupt, `/reset` to kill.",
            parse_mode="Markdown",
        )
    except FileNotFoundError:
        await msg.edit_text(
            "❌ `claude` command not found.\n"
            "Install Claude Code and make sure it's in your PATH.",
            parse_mode="Markdown",
        )
    except Exception as e:
        await msg.edit_text(f"❌ Failed to start Claude Code: {e}")


# ── Callback handler (inline buttons) ────────────────────────────────────────
async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if update.effective_user.id != TELEGRAM_USER_ID:
        return
    chat_id = update.effective_chat.id

    if query.data.startswith("cd:"):
        name = query.data[3:]
        projects = load_projects()
        path = projects.get(name)
        if not path:
            await query.edit_message_text(
                f"Project `{name}` not found.", parse_mode="Markdown"
            )
            return
        if not os.path.isdir(path):
            await query.edit_message_text(
                f"Directory not found: `{path}`\nUpdate it with `/save {name}`.",
                parse_mode="Markdown",
            )
            return
        get_state(chat_id)["cwd"] = path
        await query.edit_message_text(f"Now in: `{path}`", parse_mode="Markdown")


# ── Entry point ───────────────────────────────────────────────────────────────
def main() -> None:
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("cd", cmd_cd))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("stop", cmd_stop))
    app.add_handler(CommandHandler("reset", cmd_reset))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info(
        "Claude Code Companion (local-mode) started. Authorized user: %s",
        TELEGRAM_USER_ID,
    )
    app.run_polling(
        drop_pending_updates=True,
        allowed_updates=["message", "callback_query"],
    )


if __name__ == "__main__":
    main()
