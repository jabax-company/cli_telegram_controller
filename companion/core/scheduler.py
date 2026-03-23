"""Scheduled task management with independent per-project execution."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from companion.core.storage import audit

logger = logging.getLogger(__name__)

_SCHEDULE_FILE = Path(__file__).parent.parent.parent / ".scheduled_tasks.json"
_tasks: list[dict] = []
_running: dict[str, asyncio.Task] = {}  # task_id -> running asyncio Task


# ─── persistence ────────────────────────────────────────────────────────────


def _load_tasks() -> None:
    global _tasks
    if _SCHEDULE_FILE.exists():
        try:
            _tasks = json.loads(_SCHEDULE_FILE.read_text(encoding="utf-8"))
        except Exception:
            _tasks = []
    else:
        _tasks = []


def _save_tasks() -> None:
    try:
        _SCHEDULE_FILE.write_text(json.dumps(_tasks, indent=2), encoding="utf-8")
    except Exception as e:
        logger.warning("Could not save scheduled tasks: %s", e)


# ─── public API ─────────────────────────────────────────────────────────────


def add_task(
    chat_id: int,
    cwd: str,
    run_at: datetime,
    task_type: str,
    prompt: str,
    label: str = "",
) -> str:
    """Add a scheduled task. Returns the task ID."""
    _load_tasks()
    task_id = str(uuid.uuid4())[:8]
    _tasks.append({
        "id": task_id,
        "chat_id": chat_id,
        "cwd": cwd,
        "run_at": run_at.isoformat(),
        "type": task_type,  # "claude" or "bash"
        "prompt": prompt,
        "label": label or Path(cwd).name,
        "created_at": datetime.now().isoformat(),
    })
    _save_tasks()
    return task_id


def list_tasks(chat_id: int) -> list[dict]:
    _load_tasks()
    return [t for t in _tasks if t["chat_id"] == chat_id]


def cancel_task(chat_id: int, task_id: str) -> bool:
    _load_tasks()
    before = len(_tasks)
    _tasks[:] = [t for t in _tasks if not (t["chat_id"] == chat_id and t["id"] == task_id)]
    changed = len(_tasks) < before
    if changed:
        _save_tasks()
    return changed


# ─── argument parsing ────────────────────────────────────────────────────────


def parse_at_time(time_str: str) -> tuple[datetime | None, str]:
    """Parse HH:MM -> (datetime, error_msg). Returns tomorrow if time already passed."""
    m = re.match(r"^(\d{1,2}):(\d{2})$", time_str)
    if not m:
        return None, f"Formato de hora inválido '{time_str}'. Usa HH:MM (ej: 14:30)"
    hour, minute = int(m.group(1)), int(m.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None, "Hora inválida. Horas 0-23, minutos 0-59."
    now = datetime.now()
    run_at = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if run_at <= now:
        run_at += timedelta(days=1)
    return run_at, ""


def parse_at_args(args: list[str]) -> tuple[datetime | None, str, str | None]:
    """
    Parse /at arguments: HH:MM [DD/MM[/YYYY]] [prompt...].
    Returns (run_at, prompt, error_msg).
    prompt="" means accumulation mode was requested.
    """
    if not args:
        return None, "", "Uso: /at HH:MM [DD/MM] <prompt o /bash comando>"

    run_at, err = parse_at_time(args[0])
    if err or run_at is None:
        return None, "", err or "Hora inválida."

    remaining = args[1:]
    now = datetime.now()

    # Optional date DD/MM or DD/MM/YYYY
    if remaining and re.match(r"^\d{1,2}/\d{1,2}(/\d{4})?$", remaining[0]):
        date_str = remaining[0]
        remaining = remaining[1:]
        parts = date_str.split("/")
        day, month = int(parts[0]), int(parts[1])
        year = int(parts[2]) if len(parts) == 3 else now.year
        try:
            run_at = datetime(year, month, day, run_at.hour, run_at.minute, 0)
        except ValueError as e:
            return None, "", f"Fecha inválida: {e}"

    prompt = " ".join(remaining)
    return run_at, prompt, None


# ─── independent streaming execution ────────────────────────────────────────


async def _summarize_and_notify(
    bot, chat_id: int, task_id: str, label: str, prompt: str, full_output: str
) -> None:
    """Ask Claude to briefly summarize the task output and send completion notification."""
    import json as _json

    from companion.core.config import MAX_MSG

    summary = ""
    summary_failed = False
    if full_output.strip():
        snippet = full_output[-3000:] if len(full_output) > 3000 else full_output
        summary_prompt = (
            "El siguiente es el output de una tarea automatizada ejecutada por un bot. "
            "Resume en 2-3 frases breves qué se hizo y cuál fue el resultado. "
            "Sé conciso y directo, sin encabezados ni listas.\n\n"
            f"Tarea: {prompt[:200]}\n\nOutput:\n{snippet}"
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                "claude", "--dangerously-skip-permissions",
                "-p", summary_prompt,
                "--output-format", "json",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=90)
            raw = stdout.decode("utf-8", errors="replace").strip()
            obj = _json.loads(raw)
            summary = obj.get("result", "").strip()
        except Exception as e:
            logger.warning("_summarize_and_notify[%s] summary failed: %s", task_id, e)
            summary_failed = True

    header = f"✅ <b>Tarea [{task_id}] completada</b> — <code>{label}</code>"
    if summary:
        msg = f"{header}\n\n{summary}"
        try:
            await bot.send_message(chat_id, msg, parse_mode="HTML")
        except Exception as e:
            logger.warning("_summarize_and_notify[%s] send failed: %s", task_id, e)
    elif summary_failed and full_output.strip():
        # No AI available — send raw output instead
        try:
            await bot.send_message(chat_id, header, parse_mode="HTML")
        except Exception:
            pass
        raw_out = full_output.strip()
        chunk_size = MAX_MSG - 10
        for i in range(0, len(raw_out), chunk_size):
            chunk = raw_out[i:i + chunk_size]
            try:
                await bot.send_message(chat_id, chunk)
            except Exception as e:
                logger.warning("_summarize_and_notify[%s] raw chunk send failed: %s", task_id, e)
                break
    else:
        try:
            await bot.send_message(chat_id, header, parse_mode="HTML")
        except Exception as e:
            logger.warning("_summarize_and_notify[%s] send failed: %s", task_id, e)


async def _stream_output(bot, chat_id: int, proc, task_id: str, label: str) -> str:
    """Stream process output to Telegram independently of the main session state.
    Returns the full accumulated text output for summarization."""
    import json as _json
    from companion.core.security import strip_ansi
    from companion.core.config import FLUSH_INTERVAL, FLUSH_SIZE, KEEPALIVE_SECS

    text_buf = ""
    line_buf = ""
    full_output_parts: list[str] = []

    async def _flush() -> None:
        nonlocal text_buf
        clean = strip_ansi(text_buf).strip()
        text_buf = ""
        if not clean:
            return
        full_output_parts.append(clean)

    def _handle_line(raw: str) -> None:
        nonlocal text_buf
        raw = raw.strip()
        if not raw:
            return
        try:
            obj = _json.loads(raw)
        except _json.JSONDecodeError:
            text_buf += raw + "\n"
            return
        obj_type = obj.get("type")
        if obj_type == "assistant":
            for block in obj.get("message", {}).get("content", []):
                if block.get("type") == "text":
                    text_buf += block["text"]

    loop = asyncio.get_running_loop()
    last_flush = loop.time()
    last_keepalive = loop.time()

    try:
        while True:
            try:
                chunk = await asyncio.wait_for(proc.stdout.read(4096), timeout=FLUSH_INTERVAL)
            except asyncio.TimeoutError:
                await _flush()
                now = loop.time()
                if now - last_keepalive >= KEEPALIVE_SECS:
                    last_keepalive = now
                    try:
                        await bot.send_message(chat_id, f"⏳ [{task_id}/{label}] En progreso...")
                    except Exception:
                        pass
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
                last_flush = now

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error("stream_output[%s] error: %s", task_id, e)
    finally:
        if line_buf.strip():
            _handle_line(line_buf)
        await _flush()
        _running.pop(task_id, None)

    return "\n".join(full_output_parts)


async def _execute_independently(bot, task: dict) -> None:
    """
    Execute a scheduled task in its own subprocess, completely independent
    of the main session state so multiple projects can run concurrently.
    """
    from companion.core.claude_runtime import _detect_path_restriction_flag
    from companion.core.config import (
        BACKEND_RUNBOOK_APPEND_SYSTEM_PROMPT,
        DISALLOWED_BASH_TOOLS,
        ENFORCE_BACKEND_RUNBOOK,
        RESTRICT_PATHS,
        RUN_GUIDE_APPEND_SYSTEM_PROMPT,
        SAFE_APPEND_SYSTEM_PROMPT,
        SAFE_MODE,
    )
    from companion.core.security import blocked_match

    chat_id: int = task["chat_id"]
    cwd: str = task["cwd"]
    prompt: str = task["prompt"]
    task_type: str = task.get("type", "claude")
    task_id: str = task["id"]
    label: str = task.get("label") or Path(cwd).name

    await bot.send_message(
        chat_id,
        f"Iniciando tarea [{task_id}] — {label}\n"
        f"Tipo: {task_type} | Dir: {cwd}\n"
        f"Prompt: {prompt[:300]}",
    )
    audit(chat_id, f"SCHEDULED_{task_type.upper()} [{task_id}/{label}]: {prompt}")

    try:
        if task_type == "bash":
            matched = blocked_match(prompt)
            if matched is not None:
                await bot.send_message(
                    chat_id,
                    f"[{task_id}] Bloqueado (patrón peligroso: {matched}). Cancelado.",
                )
                return
            proc = await asyncio.create_subprocess_shell(
                prompt,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )
        else:
            from companion.core.config import AI_ENGINE, CODEX_MODEL
            if AI_ENGINE == "codex":
                cmd = ["codex", "--approval-mode", "full-auto", "-q"]
                if CODEX_MODEL:
                    cmd += ["--model", CODEX_MODEL]
                cmd.append(prompt)
                stream_format = "plain"
            else:
                cmd = [
                    "claude",
                    "--dangerously-skip-permissions",
                    "-p", prompt,
                    "--output-format", "stream-json",
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
                if RESTRICT_PATHS:
                    path_flag = await _detect_path_restriction_flag()
                    if path_flag:
                        cmd += [path_flag, cwd]
                stream_format = "json"  # noqa: F841 – _stream_output handles both formats

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                cwd=cwd,
            )

        full_output = await _stream_output(bot, chat_id, proc, task_id, label)
        await _summarize_and_notify(bot, chat_id, task_id, label, prompt, full_output)

    except FileNotFoundError:
        await bot.send_message(chat_id, f"[{task_id}] Falló: comando 'claude' no encontrado.")
    except Exception as e:
        logger.error("Scheduled task [%s] failed: %s", task_id, e)
        await bot.send_message(chat_id, f"[{task_id}] Error inesperado: {e}")


# ─── scheduler loop ──────────────────────────────────────────────────────────


def _pop_due_tasks() -> list[dict]:
    _load_tasks()
    now = datetime.now()
    due = [t for t in _tasks if datetime.fromisoformat(t["run_at"]) <= now]
    _tasks[:] = [t for t in _tasks if datetime.fromisoformat(t["run_at"]) > now]
    if due:
        _save_tasks()
    return due


async def _run_scheduler(bot) -> None:
    # Prune finished tasks from _running
    finished = [tid for tid, t in list(_running.items()) if t.done()]
    for tid in finished:
        _running.pop(tid, None)

    due = _pop_due_tasks()
    for task in due:
        task_id = task["id"]
        if task_id in _running:
            continue  # already executing
        t = asyncio.create_task(
            _execute_independently(bot, task),
            name=f"scheduled_{task_id}",
        )
        _running[task_id] = t


async def scheduler_loop(bot) -> None:
    """Background loop: checks every 30s for due tasks and fires them concurrently."""
    _load_tasks()
    try:
        while True:
            await asyncio.sleep(30)
            await _run_scheduler(bot)
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        logger.exception("Scheduler loop crashed: %s", exc)
