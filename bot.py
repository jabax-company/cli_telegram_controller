"""Claude Code Companion — local-mode bot.

Telegram <-> Claude Code non-interactive bridge (text mode).

You (Telegram) ──► claude -p "prompt" --output-format text
Claude Code output ──► Telegram (raw text chunks flushed every 2 seconds)
"""

import asyncio
import json
import logging
import os
import re
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
_SCRIPT_PARENT = Path(__file__).resolve().parent.parent
BASE_DIR = Path(os.path.expanduser(os.environ.get("INITIAL_DIR", str(_SCRIPT_PARENT))))
INITIAL_DIR = str(BASE_DIR)
RESTRICT_PATHS = os.environ.get("RESTRICT_PATHS", "false").lower() == "true"
_extra_blocked = os.environ.get("BLOCKED_PATTERNS", "")
EXTRA_BLOCKED = [p.strip() for p in _extra_blocked.split(",") if p.strip()]

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
_lvl = os.environ.get("LOG_LEVEL", "INFO").upper()
logger.setLevel(getattr(logging, _lvl, logging.INFO))

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


def resolve_path(arg: str) -> str | None:
    """Resolve a /cd argument in priority order:
    1. Saved project name
    2. Absolute path or ~-expanded path
    3. Relative to BASE_DIR
    4. Relative to home dir
    """
    projects = load_projects()
    if arg in projects:
        return projects[arg]
    expanded = os.path.expanduser(arg)
    if os.path.isabs(expanded):
        return expanded if os.path.isdir(expanded) else None
    for base in (BASE_DIR, Path.home()):
        candidate = str(base / arg)
        if os.path.isdir(candidate):
            return candidate
    return None


# ── Session state (per chat_id) ───────────────────────────────────────────────
def _blank_state() -> dict:
    return {
        "cwd": INITIAL_DIR,
        "session_active": False,
        "proc": None,           # asyncio.subprocess.Process
        "output_task": None,    # asyncio.Task
        "keepalive_task": None, # asyncio.Task
        "pending_confirm": None,  # blocked prompt awaiting explicit YES
        "session_id": None,     # Claude session ID for --resume; None = fresh
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
    clean = strip_ansi(text).strip()
    if not clean:
        return
    if len(clean) > MAX_MSG:
        clean = clean[:MAX_MSG] + "\n\n_(truncated)_"
    try:
        await bot.send_message(chat_id, clean)
    except Exception as e:
        logger.warning("send_chunk error for chat %s: %s", chat_id, e)


# ── Output reader ─────────────────────────────────────────────────────────────
async def _output_reader(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Stream Claude Code output (stream-json format) to Telegram until the process exits."""
    s = get_state(chat_id)
    proc: asyncio.subprocess.Process = s["proc"]
    bot = context.bot
    loop = asyncio.get_running_loop()
    logger.info("output_reader[%s]: started (stream-json mode)", chat_id)

    text_buf = ""   # flushed to Telegram periodically
    line_buf = ""   # accumulates bytes until newline
    last_flush = loop.time()

    async def _flush():
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
            text_buf += raw + "\n"   # not JSON — pass through as-is
            return

        obj_type = obj.get("type")
        if obj_type == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text_buf += block["text"]
        elif obj_type == "result":
            sid = obj.get("session_id")
            if sid:
                s["session_id"] = sid
                logger.info("output_reader[%s]: session_id captured: %s", chat_id, sid)
            # "result" also carries the full final text; skip — we already
            # streamed it incrementally via "assistant" messages above.
        # other types (system, tool_use, tool_result…) are silently ignored

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
                logger.info("output_reader[%s]: EOF", chat_id)
                break

            decoded = chunk.decode("utf-8", errors="replace")
            logger.info("output_reader[%s]: %d chars received", chat_id, len(decoded))
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
        # flush any remaining line_buf content
        if line_buf.strip():
            _handle_line(line_buf)
        await _flush()
        s["session_active"] = False
        s["proc"] = None
        if s.get("keepalive_task") and not s["keepalive_task"].done():
            s["keepalive_task"].cancel()
        has_session = bool(s.get("session_id"))
        hint = " Next message continues in context. /reset to start fresh." if has_session else ""
        try:
            await bot.send_message(chat_id, f"✅ Done.{hint}")
        except Exception:
            pass


async def _keepalive(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        while True:
            await asyncio.sleep(KEEPALIVE_SECS)
            s = get_state(chat_id)
            if not s["session_active"]:
                return
            await context.bot.send_message(chat_id, "⏳ Still working...")
    except asyncio.CancelledError:
        pass


# ── Spawn Claude Code ─────────────────────────────────────────────────────────
async def spawn_claude(
    chat_id: int, prompt: str, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Spawn claude -p <prompt> --output-format stream-json and wire it to Telegram."""
    s = get_state(chat_id)
    cwd = s["cwd"]

    cmd = [
        "claude",
        "--dangerously-skip-permissions",
        "-p", prompt,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
    ]
    if s["session_id"]:
        cmd += ["--resume", s["session_id"]]
    if RESTRICT_PATHS:
        cmd += ["--allowedPaths", cwd]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=cwd,
    )
    s["proc"] = proc
    s["session_active"] = True

    loop = asyncio.get_running_loop()
    s["output_task"] = loop.create_task(_output_reader(chat_id, context))
    s["keepalive_task"] = loop.create_task(_keepalive(chat_id, context))


# ── Command handlers ──────────────────────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    await update.message.reply_text(
        "*Claude Code Companion*\n\n"
        f"Base dir: `{BASE_DIR}`\n"
        f"Current dir: `{s['cwd']}`\n\n"
        "*Navigation:*\n"
        "• `/cd` — go to base dir\n"
        "• `/cd myapp` — switch to BASE\\_DIR/myapp (auto-resolved)\n"
        "• `/cd ~/other/path` — full path\n"
        "• `/cd saved-name` — jump to a saved project\n"
        "• `/paths` — see all available directories\n"
        "• `/projects` — tap to switch between saved projects\n"
        "• `/save myapp` — save current dir as a named project\n\n"
        "*Session:*\n"
        "• `/status` — current dir + active/idle + session state\n"
        "• `/plan <task>` — ask Claude to plan without making changes\n"
        "• `/bash <cmd>` — run a shell command directly (e.g. `/bash python hello.py`)\n"
        "• `/stop` — Ctrl+C to Claude Code\n"
        "• `/reset` — kill session + clear context, stay in same dir\n\n"
        "*Example conversation:*\n"
        "```\n"
        "You:  /cd claude_code_bot\n"
        "Bot:  Now in: .../dev/claude_code_bot\n\n"
        "You:  create hello.py that prints hello world\n"
        "Bot:  Starting Claude Code...\n"
        "Bot:  [Claude streams output here]\n"
        "Bot:  ✅ Done. Next message continues in context. /reset to start fresh.\n\n"
        "You:  /bash python hello.py\n"
        "Bot:  $ python hello.py\n"
        "Bot:  hello world\n"
        "Bot:  ✅ Done.\n\n"
        "You:  now add a name parameter\n"
        "Bot:  [Claude remembers hello.py natively via --resume]\n"
        "Bot:  ✅ Done. Next message continues in context. /reset to start fresh.\n\n"
        "You:  /reset\n"
        "Bot:  Session reset. Context cleared.\n"
        "```",
        parse_mode="Markdown",
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    icon = "🟢 Active" if s["session_active"] else "⚫ Idle"
    has_ctx = bool(s.get("session_id"))
    ctx_line = "Context: session active (resumable)" if has_ctx else "Context: fresh"
    await update.message.reply_text(
        f"*Status*\n"
        f"Directory: `{s['cwd']}`\n"
        f"Session: {icon}\n"
        f"{ctx_line}",
        parse_mode="Markdown",
    )


async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    if not context.args:
        get_state(chat_id)["cwd"] = str(BASE_DIR)
        await update.message.reply_text(f"Now in: `{BASE_DIR}`", parse_mode="Markdown")
        return
    arg = " ".join(context.args)
    target = resolve_path(arg)
    if target is None:
        await update.message.reply_text(
            f"Directory not found: `{arg}`", parse_mode="Markdown"
        )
        return
    get_state(chat_id)["cwd"] = target
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


async def cmd_paths(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    projects = load_projects()
    lines = []

    if projects:
        lines.append("*Saved projects:*")
        for name, path in projects.items():
            lines.append(f"  `/cd {name}` → `{path}`")

    base_subdirs = []
    try:
        base_subdirs = sorted(d.name for d in BASE_DIR.iterdir() if d.is_dir())
    except Exception:
        pass

    if base_subdirs:
        lines.append(f"\n*Folders in `{BASE_DIR}`:*")
        for d in base_subdirs:
            lines.append(f"  `/cd {d}`")

    if not lines:
        lines.append(f"No saved projects and no subdirectories found in `{BASE_DIR}`.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: `/plan <task description>`", parse_mode="Markdown")
        return
    chat_id = update.effective_chat.id
    s = get_state(chat_id)
    if s["session_active"]:
        await update.message.reply_text(
            "A session is already active. Use /stop or /reset first."
        )
        return
    task = " ".join(context.args)
    prompt = (
        "Please plan the following task step by step. "
        "Do NOT execute any code, modify any files, or run any commands. "
        "Just outline what you would do and why:\n\n" + task
    )
    audit(chat_id, f"PLAN: {task}")
    await _run_task(chat_id, prompt, context, update)


async def cmd_bash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    s = get_state(chat_id)

    if s["session_active"]:
        await update.message.reply_text("⏳ Claude is working. Use /stop first.")
        return

    if not context.args:
        await update.message.reply_text(
            "Usage: `/bash <command>`\nExample: `/bash python hello.py`",
            parse_mode="Markdown",
        )
        return

    cmd_str = " ".join(context.args)

    if is_blocked(cmd_str):
        await update.message.reply_text(
            "⚠️ Blocked pattern detected. Command not run.",
        )
        return

    audit(chat_id, f"BASH: {cmd_str}")
    await update.message.reply_text(f"$ `{cmd_str}`", parse_mode="Markdown")

    proc = await asyncio.create_subprocess_shell(
        cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=s["cwd"],
    )
    s["proc"] = proc
    s["session_active"] = True

    loop = asyncio.get_running_loop()
    s["output_task"] = loop.create_task(_output_reader(chat_id, context))
    s["keepalive_task"] = loop.create_task(_keepalive(chat_id, context))


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    s = get_state(update.effective_chat.id)
    if not (s["session_active"] and s["proc"]):
        await update.message.reply_text("No active session to stop.")
        return
    try:
        s["proc"].terminate()
        await update.message.reply_text("Sent terminate to Claude Code.")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}")


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
    await update.message.reply_text("Session reset. Context cleared. Ready for a new task.")


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

    # ── Active session: non-interactive, can't relay input ───────────────
    if s["session_active"]:
        await update.message.reply_text("⏳ Claude is working. Please wait...")
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
    app.add_handler(CommandHandler("paths", cmd_paths))
    app.add_handler(CommandHandler("projects", cmd_projects))
    app.add_handler(CommandHandler("save", cmd_save))
    app.add_handler(CommandHandler("plan", cmd_plan))
    app.add_handler(CommandHandler("bash", cmd_bash))
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
