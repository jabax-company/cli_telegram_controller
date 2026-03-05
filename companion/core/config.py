"""Environment and runtime configuration."""

from __future__ import annotations

import logging
import os
import re
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT_DIR = Path(__file__).resolve().parents[2]
IMAGES_DIR = ROOT_DIR / "images"
IMAGES_DIR.mkdir(parents=True, exist_ok=True)

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_USER_ID = int(os.environ["TELEGRAM_USER_ID"])

DEFAULT_BASE = ROOT_DIR
BASE_DIR = Path(os.path.expanduser(os.environ.get("INITIAL_DIR", str(DEFAULT_BASE)))).resolve()
INITIAL_DIR = str(BASE_DIR)

RESTRICT_PATHS = os.environ.get("RESTRICT_PATHS", "false").lower() == "true"
SAFE_MODE = os.environ.get("SAFE_MODE", "true").lower() == "true"
_extra_blocked = os.environ.get("BLOCKED_PATTERNS", "")
EXTRA_BLOCKED = [p.strip() for p in _extra_blocked.split(",") if p.strip()]
INACTIVITY_TIMEOUT_SECS = int(os.environ.get("INACTIVITY_TIMEOUT_SECS", "1800"))
INACTIVITY_CHECK_SECS = int(os.environ.get("INACTIVITY_CHECK_SECS", "30"))
MAX_IMAGE_HISTORY = int(os.environ.get("MAX_IMAGE_HISTORY", "50"))
MAX_PENDING_IMAGES = int(os.environ.get("MAX_PENDING_IMAGES", "10"))
TRAY_ICON_ENABLED = os.environ.get("TRAY_ICON_ENABLED", "true").lower() == "true"

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "small").strip()
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "auto").strip()
WHISPER_COMPUTE_TYPE = os.environ.get("WHISPER_COMPUTE_TYPE", "int8").strip()
WHISPER_BEAM_SIZE = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
WHISPER_VAD_FILTER = os.environ.get("WHISPER_VAD_FILTER", "true").lower() == "true"
WHISPER_PRIMARY_LANGUAGE = os.environ.get("WHISPER_PRIMARY_LANGUAGE", "es").strip()
WHISPER_ALLOW_AUTO_FALLBACK = (
    os.environ.get("WHISPER_ALLOW_AUTO_FALLBACK", "true").lower() == "true"
)

FULLSTACK_FRONT_CMD = os.environ.get("FULLSTACK_FRONT_CMD", "").strip()
FULLSTACK_BACK_CMD = os.environ.get("FULLSTACK_BACK_CMD", "").strip()
FULLSTACK_FRONT_DIR = os.environ.get("FULLSTACK_FRONT_DIR", "").strip()
FULLSTACK_BACK_DIR = os.environ.get("FULLSTACK_BACK_DIR", "").strip()

BACKEND_RUNBOOK_FILE = os.environ.get("BACKEND_RUNBOOK_FILE", ".claude/backend_run.json").strip()
SERVER_CONFIG_FILE = os.environ.get("SERVER_CONFIG_FILE", ".claude/server.json").strip()
ENFORCE_BACKEND_RUNBOOK = os.environ.get("ENFORCE_BACKEND_RUNBOOK", "true").lower() == "true"
BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT = (
    "When creating or modifying backend/API code, update this repository file: "
    f"'{BACKEND_RUNBOOK_FILE}'. "
    "The file must be valid JSON with keys: "
    "command (required string), workdir (optional relative path), "
    "port (optional number), api_prefix (optional string). "
    "Keep it accurate for local startup."
)

RUN_GUIDE_APPEND_SYSTEM_PROMPT = (
    "When reporting completed code changes, include exact local run instructions. "
    "If the project has frontend and backend, provide explicit commands for both, "
    "where to run them (repo root or subfolder), expected ports, and the matching "
    "Telegram publish command /server fullstack <front_port> <backend_port> "
    "(with api_prefix if needed)."
)

DATA_DIR = Path.home() / ".claude_code_bot"
PROJECTS_FILE = DATA_DIR / "projects.json"
AUDIT_LOG = DATA_DIR / "audit.log"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
for _noisy in ("httpx", "telegram", "telegram.ext.Application"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)
_log_level = os.environ.get("LOG_LEVEL", "INFO").upper()
logging.getLogger().setLevel(getattr(logging, _log_level, logging.INFO))

URL_RE = re.compile(r"https://[a-z0-9\-]+\.trycloudflare\.com")
TEST_SERVE = str(ROOT_DIR / "test_serve.py")
FULLSTACK_PROXY = str(ROOT_DIR / "fullstack_proxy.py")

SERVE_PORT = int(os.environ.get("SERVE_PORT", "8080"))
BROWSE_PAGE_SIZE = 8

MAX_MSG = 3500
KEEPALIVE_SECS = 30
FLUSH_INTERVAL = 2.0
FLUSH_SIZE = 500

DISALLOWED_BASH_TOOLS: list[str] = [
    "Bash(rm:*)",
    "Bash(del:*)",
    "Bash(erase:*)",
    "Bash(rmdir:*)",
    "Bash(rd:*)",
    "Bash(unlink:*)",
    "Bash(shred:*)",
    "Bash(sdelete:*)",
    "Bash(Remove-Item:*)",
    "Bash(git reset --hard:*)",
    "Bash(git clean:*)",
]

SAFE_APPEND_SYSTEM_PROMPT = (
    "Critical safety rule: never run file or directory deletion commands "
    "(rm, del, erase, rmdir, rd, unlink, shred, sdelete, Remove-Item, git reset --hard, "
    "git clean). If cleanup is needed, ask the user first and wait for explicit confirmation."
)
