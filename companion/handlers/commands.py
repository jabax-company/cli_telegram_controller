"""Command handlers."""

from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from companion.core.auth import is_authorized
from companion.core.browser import build_browser_view, start_browser
from companion.core.claude_runtime import (
    keepalive,
    output_reader,
    run_task,
)
from companion.core.config import BASE_DIR, BASH_TIMEOUT_SECS, ENABLE_BASH
from companion.core.paths import resolve_path
from companion.core.prompt_optimizer import clear_prompt_intake
from companion.core.runtime_control import request_stop
from companion.core.security import blocked_match, blocked_reply_text, register_blocked_confirm
from companion.core.state import get_state, maybe_inject_resume_prompt, reset_chat_state
from companion.core.storage import audit, load_projects, save_projects


async def _run_git(cwd: str, *args: str) -> tuple[int, str, str]:
    def _run_sync() -> tuple[int, str, str]:
        completed = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
        )
        return (
            int(completed.returncode),
            completed.stdout.strip(),
            completed.stderr.strip(),
        )

    return await asyncio.to_thread(_run_sync)


async def _switch_branch(cwd: str, branch_name: str, create: bool) -> tuple[int, str, str]:
    if create:
        rc, out, err = await _run_git(cwd, "switch", "-c", branch_name)
        if rc != 0:
            rc, out, err = await _run_git(cwd, "checkout", "-b", branch_name)
        return rc, out, err

    rc, out, err = await _run_git(cwd, "switch", branch_name)
    if rc != 0:
        rc, out, err = await _run_git(cwd, "checkout", branch_name)
    return rc, out, err


async def _ensure_branch_lock(state: dict) -> tuple[bool, str | None]:
    locked_branch = (state.get("branch_lock") or "").strip()
    locked_repo = (state.get("branch_repo") or "").strip()
    if not locked_branch or not locked_repo:
        return True, None

    rc, repo_now, err = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
    if rc != 0:
        return (
            False,
            "Branch lock is active, but current folder is not a Git repository.\n"
            "Use /cd back to that repo or run /branch off.",
        )

    repo_now_resolved = str(Path(repo_now).resolve())
    if repo_now_resolved != locked_repo:
        return (
            False,
            "Branch lock belongs to another repository.\n"
            f"Locked repo: {locked_repo}\n"
            f"Current repo: {repo_now_resolved}\n"
            "Run /branch <name> again in this repo or /branch off.",
        )

    rc, current_branch, err = await _run_git(state["cwd"], "branch", "--show-current")
    if rc != 0:
        return False, f"Could not read current branch: {err or current_branch}"
    if current_branch == locked_branch:
        return True, None

    rc, out, err = await _switch_branch(state["cwd"], locked_branch, create=False)
    if rc != 0:
        return False, f"Could not switch to locked branch '{locked_branch}': {err or out}"

    return True, f"Auto-switched to locked branch: {locked_branch}"


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    state = get_state(update.effective_chat.id)
    await update.effective_message.reply_text(
        "Claude Code Companion\n\n"
        f"Base dir: {state['base_dir']}\n"
        f"Current dir: {state['cwd']}\n\n"
        "Workflow:\n"
        "1) Send /claude to start prompt mode.\n"
        "2) Send your prompt.\n"
        "3) Answer 3 to 5 optimization questions.\n"
        "4) Send /claude again to execute optimized prompt.\n\n"
        "Image flow:\n"
        "- Send an image and it is saved in repo root /images.\n"
        "- If image has no caption, bot waits for your next prompt and includes it.\n"
        "- Use <image> in text to explicitly reference the latest stored image.\n\n"
        "Key commands:\n"
        "/base [path|reset] - show or change base directory\n"
        "/cd <path-or-name> - change directory\n"
        "/3d <path-or-name> - alias of /cd (voice-friendly)\n"
        "/branch <name> - create/switch branch and lock Claude changes to it\n"
        "/claude - run pending prompt\n"
        "/claude <text> - run explicit prompt now\n"
        "/paths [path] - browse folders with buttons\n"
        "/server - publish current folder (static)\n"
        "/server proxy <port> - publish existing local backend\n"
        "/server run <port> <command> - run backend and publish it\n"
        "/server fullstack <front_port> <backend_port> [api_prefix] - one URL for front+backend\n"
        "/bot stop - stop the Telegram bot process\n"
        "/status - Claude /status in Claude mode, bot status otherwise\n"
        "/status bot - force bot status\n"
        "/help - full command list"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    state = get_state(update.effective_chat.id)
    await update.effective_message.reply_text(
        "Command reference\n\n"
        f"Base dir: {state['base_dir']}\n"
        f"Current dir: {state['cwd']}\n\n"
        "Navigation\n"
        "/cd - go to base dir\n"
        "/cd <path-or-name> - change directory\n"
        "/3d - alias of /cd\n"
        "/base [path|reset] - show or change base directory\n"
        "/mkdir <name> - create and enter folder\n"
        "/paths [path] - interactive folder browser\n"
        "/projects - list saved projects\n"
        "/save <name> - save current directory\n\n"
        "Execution\n"
        "/branch <name> - create/switch branch and lock Claude changes to it\n"
        "/branch off - clear branch lock\n"
        "/claude - run pending prompt\n"
        "/claude <prompt> - run immediately\n"
        "/plan <task> - ask Claude to plan only\n"
        "/bash <cmd> - run shell command in current dir\n"
        "/stop - stop running process\n"
        "/reset - clear running process and context\n\n"
        "Preview\n"
        "/server - start static server + Cloudflare tunnel\n"
        "/server proxy <port> - publish an already running local service\n"
        "/server run <port> <command> - run command and publish port\n"
        "/server fullstack <front_port> <backend_port> [api_prefix] - proxy front+backend under one URL\n"
        "/server help - get a Claude prompt to configure auto-deployment\n"
        "/server status - show tunnel status\n"
        "/server stop - stop tunnel\n\n"
        "Computer control\n"
        "/sysinfo - CPU, RAM, disk, battery, uptime\n"
        "/screenshot - capture screen and send it here\n"
        "/ps [name] - top processes (optional name filter)\n"
        "/kill <pid> - terminate a process (asks confirmation)\n"
        "/lock - lock the computer screen\n"
        "/download <path> - send a file from the computer to this chat\n"
        "Send any document (non-image) and it is saved to <cwd>/incoming/.\n\n"
        "Bot control\n"
        "/bot stop - stop this bot process\n\n"
        "Status behavior\n"
        "In Claude mode, /status is passed through to Claude.\n"
        "Use /status bot to show Telegram bot local state.\n\n"
        "Voice\n"
        "Send a Telegram voice/audio message to transcribe with local faster-whisper.\n"
        "The transcript is saved as pending prompt; run with /claude.\n"
        "Default local model is 'small' (configurable via WHISPER_MODEL).\n\n"
        "Images\n"
        "Send a photo/image-document and it is stored under repo root /images.\n"
        "If sent while Claude mode is active, caption text runs immediately with image context.\n"
        "Without caption, the image is queued for your next prompt."
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    args = context.args or []

    # In Claude mode, /status should go to Claude unless user explicitly requests bot status.
    if state.get("claude_mode") and not (args and args[0].lower() in {"bot", "local"}):
        if state.get("pending_confirm"):
            await update.effective_message.reply_text("Please reply YES or cancel first.")
            return
        if state.get("session_active"):
            await update.effective_message.reply_text("Claude is working. Please wait.")
            return
        prompt = maybe_inject_resume_prompt(state, "/status")
        audit(chat_id, f"CLAUDE_MODE_COMMAND: {prompt}")
        await run_task(chat_id, prompt, context, update)
        return

    icon = "ACTIVE" if state["session_active"] else "IDLE"
    has_ctx = bool(state.get("session_id"))
    pending = "yes" if state.get("pending_prompt") else "no"
    pending_images = len(state.get("pending_images") or [])
    intake = "ACTIVE" if state.get("prompt_intake_active") else "IDLE"
    branch_lock = state.get("branch_lock")
    branch_repo = state.get("branch_repo")
    if branch_lock and branch_repo:
        branch_line = f"{branch_lock} ({branch_repo})"
    else:
        branch_line = "none"
    await update.effective_message.reply_text(
        "Status\n"
        f"Base dir: {state['base_dir']}\n"
        f"Directory: {state['cwd']}\n"
        f"Session: {icon}\n"
        f"Prompt mode: {intake}\n"
        f"Context resumable: {has_ctx}\n"
        f"Pending prompt: {pending}\n"
        f"Pending images: {pending_images}\n"
        f"Branch lock: {branch_line}"
    )


async def cmd_branch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state["session_active"]:
        await update.effective_message.reply_text("Claude is running. Use /stop first.")
        return

    if not context.args:
        rc, repo_root, err = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
        if rc != 0:
            await update.effective_message.reply_text(
                "Current directory is not a Git repository.\n"
                "Use /cd to enter your repo, then /branch <name>."
            )
            return
        rc, current_branch, err = await _run_git(state["cwd"], "branch", "--show-current")
        if rc != 0:
            await update.effective_message.reply_text(f"Could not read current branch: {err}")
            return
        repo_root_resolved = str(Path(repo_root).resolve())
        locked = state.get("branch_lock")
        locked_repo = state.get("branch_repo")
        auto_enabled = False
        if not locked or not locked_repo:
            state["branch_lock"] = current_branch
            state["branch_repo"] = repo_root_resolved
            locked = current_branch
            locked_repo = repo_root_resolved
            auto_enabled = True
        if locked and locked_repo:
            lock_line = f"{locked} ({locked_repo})"
        else:
            lock_line = "none"
        msg = (
            f"Repo: {str(Path(repo_root).resolve())}\n"
            f"Current branch: {current_branch}\n"
            f"Branch lock: {lock_line}"
        )
        if auto_enabled:
            msg += "\nBranch lock was empty and is now auto-enabled on current branch."
        await update.effective_message.reply_text(msg)
        return

    branch_name = " ".join(context.args).strip()
    if branch_name.lower() in {"off", "unlock", "none", "reset"}:
        state["branch_lock"] = None
        state["branch_repo"] = None
        await update.effective_message.reply_text("Branch lock cleared.")
        return

    rc, repo_root, err = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
    if rc != 0:
        await update.effective_message.reply_text(
            "Current directory is not a Git repository.\n"
            "Use /cd to enter your repo, then /branch <name>."
        )
        return
    repo_root = str(Path(repo_root).resolve())

    rc, _, err = await _run_git(state["cwd"], "check-ref-format", "--branch", branch_name)
    if rc != 0:
        await update.effective_message.reply_text(
            f"Invalid branch name: {branch_name}\n{err or ''}".strip()
        )
        return

    rc, _, _ = await _run_git(
        state["cwd"],
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{branch_name}",
    )
    exists = rc == 0
    rc, out, err = await _switch_branch(state["cwd"], branch_name, create=not exists)
    if rc != 0:
        await update.effective_message.reply_text(
            f"Could not switch/create branch '{branch_name}':\n{err or out}"
        )
        return

    rc, current_branch, err = await _run_git(state["cwd"], "branch", "--show-current")
    if rc != 0 or not current_branch:
        await update.effective_message.reply_text(
            f"Branch operation executed, but could not verify current branch: {err or out}"
        )
        return

    state["branch_lock"] = current_branch
    state["branch_repo"] = repo_root
    action = "Switched to existing branch" if exists else "Created and switched to new branch"
    await update.effective_message.reply_text(
        f"{action}: {current_branch}\n"
        f"Repo: {repo_root}\n"
        "Branch lock enabled. Next /claude runs in this repo will use this branch."
    )


async def cmd_cd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    if not context.args:
        state["cwd"] = str(Path(state["base_dir"]).resolve())
        await update.effective_message.reply_text(f"Now in: {state['cwd']}")
        return

    arg = " ".join(context.args)
    target = resolve_path(arg, current_dir=state["cwd"], base_dir=state["base_dir"])
    if target is None:
        await update.effective_message.reply_text(f"Directory not found: {arg}")
        return
    state["cwd"] = target
    await update.effective_message.reply_text(f"Now in: {target}")


async def cmd_base(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if not context.args:
        await update.effective_message.reply_text(
            f"Base dir: {state['base_dir']}\nCurrent dir: {state['cwd']}"
        )
        return

    arg = " ".join(context.args).strip()
    if arg.lower() == "reset":
        state["base_dir"] = str(BASE_DIR)
        state["cwd"] = str(BASE_DIR)
        await update.effective_message.reply_text(f"Base dir reset to: {state['base_dir']}")
        return

    target = resolve_path(arg, current_dir=state["cwd"], base_dir=state["base_dir"])
    if target is None:
        await update.effective_message.reply_text(f"Directory not found: {arg}")
        return

    state["base_dir"] = target
    state["cwd"] = target
    await update.effective_message.reply_text(
        f"Base dir changed to: {state['base_dir']}\nCurrent dir: {state['cwd']}"
    )


async def cmd_mkdir(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /mkdir <folder-name>")
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    name = " ".join(context.args)
    new_path = os.path.join(state["cwd"], name)
    already_existed = os.path.isdir(new_path)
    try:
        os.makedirs(new_path, exist_ok=True)
    except Exception as e:
        await update.effective_message.reply_text(f"Could not create directory: {e}")
        return
    state["cwd"] = str(Path(new_path).resolve())
    verb = "Already exists" if already_existed else "Created"
    await update.effective_message.reply_text(f"{verb}, switched to: {state['cwd']}")


async def cmd_projects(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    projects = load_projects()
    if not projects:
        await update.effective_message.reply_text(
            "No saved projects yet.\nUse /save <name> to save current directory."
        )
        return
    keyboard = [
        [InlineKeyboardButton(f"[DIR] {name}", callback_data=f"cd:{name}")]
        for name in projects
    ]
    lines = "\n".join(f"- {name} -> {path}" for name, path in projects.items())
    await update.effective_message.reply_text(
        f"Saved projects:\n{lines}",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_save(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /save <name>")
        return
    name = context.args[0]
    state = get_state(update.effective_chat.id)
    projects = load_projects()
    projects[name] = state["cwd"]
    save_projects(projects)
    await update.effective_message.reply_text(f"Saved {name} -> {state['cwd']}")


async def cmd_paths(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if context.args:
        target = resolve_path(
            " ".join(context.args),
            current_dir=state["cwd"],
            base_dir=state["base_dir"],
        )
        if target is None:
            await update.effective_message.reply_text("Directory not found.")
            return
        start_path = target
    else:
        start_path = state["cwd"]

    start_browser(chat_id, start_path)
    text, markup = build_browser_view(chat_id)
    await update.effective_message.reply_text(text, reply_markup=markup)


async def cmd_claude(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state["session_active"]:
        await update.effective_message.reply_text("Claude is already running. Use /stop first.")
        return

    explicit_prompt = " ".join(context.args).strip() if context.args else ""

    if not explicit_prompt:
        state["claude_mode"] = True
        state["inject_resume_next"] = bool(state.get("session_id"))
        clear_prompt_intake(state)
        ctx_hint = " Previous context active (--resume)." if state.get("session_id") else ""
        resume_hint = " I will inject /resume in your next Claude message." if state.get("session_id") else ""
        await update.effective_message.reply_text(
            f"Claude mode active in {state['cwd']}.\n"
            f"Every message goes directly to Claude.{ctx_hint}{resume_hint}\n"
            "/exit to leave."
        )
        return

    state["claude_mode"] = True
    state["inject_resume_next"] = bool(state.get("session_id"))
    clear_prompt_intake(state)

    ok, msg = await _ensure_branch_lock(state)
    if not ok:
        await update.effective_message.reply_text(msg or "Branch lock validation failed.")
        return
    if msg:
        await update.effective_message.reply_text(msg)

    matched = blocked_match(explicit_prompt)
    if matched is not None:
        register_blocked_confirm(state, explicit_prompt)
        audit(chat_id, f"BLOCKED_PROMPT: {explicit_prompt}")
        await update.effective_message.reply_text(blocked_reply_text(matched))
        return

    prompt = maybe_inject_resume_prompt(state, explicit_prompt)
    audit(chat_id, f"PROMPT: {prompt}")
    await run_task(chat_id, prompt, context, update)


async def cmd_exit(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    if not state.get("claude_mode"):
        await update.effective_message.reply_text("Not in Claude mode.")
        return
    state["claude_mode"] = False
    ctx_hint = " Context preserved — /claude to resume." if state.get("session_id") else ""
    await update.effective_message.reply_text(f"Exited Claude mode.{ctx_hint}")


async def cmd_plan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.effective_message.reply_text("Usage: /plan <task description>")
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    if state["session_active"]:
        await update.effective_message.reply_text("A session is already active. Use /stop or /reset.")
        return
    task = " ".join(context.args)
    prompt = (
        "Please plan the following task step by step. "
        "Do NOT execute code, modify files, or run commands.\n\n"
        f"{task}"
    )
    clear_prompt_intake(state)
    state["pending_prompt"] = prompt
    audit(chat_id, f"PENDING_PLAN: {task}")
    await update.effective_message.reply_text(
        "Plan prompt saved. Send /claude to execute it."
    )


async def cmd_bash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state["session_active"]:
        await update.effective_message.reply_text("Claude is running. Use /stop first.")
        return

    if not ENABLE_BASH:
        await update.effective_message.reply_text(
            "/bash is disabled (ENABLE_BASH=false). Use /claude instead."
        )
        return

    if not context.args:
        await update.effective_message.reply_text("Usage: /bash <command>")
        return

    cmd_str = " ".join(context.args)
    matched = blocked_match(cmd_str)
    if matched is not None:
        audit(chat_id, f"BLOCKED_BASH: {cmd_str}")
        await update.effective_message.reply_text(
            "Blocked pattern detected. Command not run.\n"
            f"Match: {matched}"
        )
        return

    audit(chat_id, f"BASH: {cmd_str}")
    await update.effective_message.reply_text(f"$ {cmd_str}")

    proc = await asyncio.create_subprocess_shell(
        cmd_str,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=state["cwd"],
    )
    state["proc"] = proc
    state["session_active"] = True
    loop = asyncio.get_running_loop()
    state["output_task"] = loop.create_task(output_reader(chat_id, context))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, context))
    loop.create_task(_bash_timeout_watchdog(proc, context, chat_id))


async def _bash_timeout_watchdog(proc, context, chat_id: int) -> None:
    try:
        await asyncio.wait_for(proc.wait(), timeout=BASH_TIMEOUT_SECS)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        audit(chat_id, f"BASH_TIMEOUT: killed after {BASH_TIMEOUT_SECS}s")
        try:
            await context.bot.send_message(
                chat_id, f"/bash command killed after {BASH_TIMEOUT_SECS}s timeout."
            )
        except Exception:
            pass


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    state = get_state(update.effective_chat.id)
    if not (state["session_active"] and state["proc"]):
        await update.effective_message.reply_text("No active session to stop.")
        return
    try:
        state["proc"].terminate()
        await update.effective_message.reply_text("Sent terminate signal.")
    except Exception as e:
        await update.effective_message.reply_text(f"Error: {e}")


async def cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    cwd = state["cwd"]

    if state["proc"]:
        try:
            state["proc"].kill()
        except Exception:
            pass
    for task in (state.get("output_task"), state.get("keepalive_task")):
        if task and not task.done():
            task.cancel()

    reset_chat_state(chat_id, cwd=cwd)
    await update.effective_message.reply_text("Session reset. Context cleared.")


async def cmd_bot(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    args = context.args or []
    if not args:
        await update.effective_message.reply_text(
            "Usage:\n"
            "/bot stop"
        )
        return

    sub = args[0].strip().lower()
    if sub != "stop":
        await update.effective_message.reply_text(
            "Usage:\n"
            "/bot stop"
        )
        return

    await update.effective_message.reply_text("Stopping bot process...")
    request_stop("telegram_bot_stop")


__all__ = [
    "cmd_start",
    "cmd_help",
    "cmd_status",
    "cmd_cd",
    "cmd_base",
    "cmd_mkdir",
    "cmd_paths",
    "cmd_projects",
    "cmd_save",
    "cmd_branch",
    "cmd_claude",
    "cmd_exit",
    "cmd_plan",
    "cmd_bash",
    "cmd_stop",
    "cmd_reset",
    "cmd_bot",
    "run_task",
]
