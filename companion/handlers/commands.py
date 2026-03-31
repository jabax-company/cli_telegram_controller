"""Command handlers."""

from __future__ import annotations

import asyncio
import html
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
from companion.core.send_adapter import TelegramSendAdapter
from companion.core.config import AI_ENGINE, BASE_DIR
from companion.core.paths import resolve_path
from companion.core.prompt_optimizer import clear_prompt_intake
from companion.core.runtime_control import request_stop
from companion.core.security import blocked_match
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
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    engine = (state.get("ai_engine") or AI_ENGINE).upper()

    keyboard = [
        [
            InlineKeyboardButton("📁 Explorar carpetas", callback_data="quick:paths"),
            InlineKeyboardButton("📋 Proyectos", callback_data="quick:projects"),
        ],
        [
            InlineKeyboardButton("📊 Estado", callback_data="quick:status"),
            InlineKeyboardButton("🕐 Programadas", callback_data="quick:scheduled"),
        ],
        [
            InlineKeyboardButton(
                "✅ CLAUDE" if engine == "CLAUDE" else "Claude Code",
                callback_data="engine:claude",
            ),
            InlineKeyboardButton(
                "✅ CODEX" if engine == "CODEX" else "Codex",
                callback_data="engine:codex",
            ),
        ],
    ]

    await update.effective_message.reply_text(
        f"<b>AI Code Companion</b>  <i>[{engine}]</i>\n\n"
        f"<b>Dir actual:</b> <code>{html.escape(state['cwd'])}</code>\n\n"
        "<b>Flujo rápido:</b>\n"
        "1. Selecciona carpeta con 📁 o <code>/cd &lt;ruta&gt;</code>\n"
        "2. Envía <code>/claude &lt;prompt&gt;</code> para ejecutar\n"
        "   — o <code>/claude</code> para el modo asistido con preguntas\n"
        "3. Texto, audio y fotos se aceptan como prompt\n\n"
        "<b>Comandos esenciales:</b>\n"
        "<code>/cd &lt;ruta&gt;</code>  — cambiar directorio\n"
        "<code>/claude &lt;prompt&gt;</code>  — ejecutar con IA\n"
        "<code>/bash &lt;cmd&gt;</code>  — ejecutar shell\n"
        "<code>/at HH:MM &lt;prompt&gt;</code>  — programar tarea\n"
        "<code>/branch &lt;nombre&gt;</code>  — rama Git y bloqueo\n"
        "<code>/server</code>  — publicar web vía Cloudflare\n\n"
        "<code>/help</code> para la referencia completa de comandos.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    state = get_state(update.effective_chat.id)
    engine = AI_ENGINE.upper()
    await update.effective_message.reply_text(
        f"<b>Referencia de comandos</b>  <i>[Motor: {engine}]</i>\n\n"
        f"<b>Dir base:</b> <code>{html.escape(state['base_dir'])}</code>\n"
        f"<b>Dir actual:</b> <code>{html.escape(state['cwd'])}</code>\n\n"

        "<b>── Navegación ──────────────────</b>\n"
        "<code>/cd</code>  — ir al dir base\n"
        "<code>/cd &lt;ruta&gt;</code>  — cambiar directorio\n"
        "<code>/base [ruta|reset]</code>  — ver/cambiar directorio base\n"
        "<code>/paths [ruta]</code>  — explorador de carpetas interactivo\n"
        "<code>/projects</code>  — listar proyectos guardados\n"
        "<code>/save &lt;nombre&gt;</code>  — guardar directorio actual\n\n"

        "<b>── Ejecución IA ─────────────────</b>\n"
        "<code>/claude</code>  — ejecutar prompt pendiente con Claude Code\n"
        "<code>/claude &lt;prompt&gt;</code>  — ejecutar directamente con Claude Code\n"
        "<code>/codex</code>  — ejecutar prompt pendiente con Codex\n"
        "<code>/codex &lt;prompt&gt;</code>  — ejecutar directamente con Codex\n"
        "<code>/bash &lt;cmd&gt;</code>  — comando shell en dir actual\n"
        "<code>/branch &lt;nombre&gt;</code>  — crear/cambiar rama Git y bloquear\n"
        "<code>/branch off</code>  — quitar bloqueo de rama\n"
        "<code>/exit</code>  — salir del modo Claude\n"
        "<code>/stop</code>  — interrumpir ejecución\n"
        "<code>/reset</code>  — borrar proceso y contexto\n\n"

        "<b>── Tareas programadas ───────────</b>\n"
        "<code>/at HH:MM &lt;prompt&gt;</code>  — programar ahora\n"
        "<code>/at HH:MM</code>  — modo acumulación (texto + audio)\n"
        "<code>/at HH:MM DD/MM &lt;prompt&gt;</code>  — fecha específica\n"
        "<code>/at HH:MM /bash &lt;cmd&gt;</code>  — programar bash\n"
        "<code>/at done</code>  — guardar acumulación\n"
        "<code>/scheduled</code>  — ver tareas pendientes\n"
        "<code>/unschedule &lt;id&gt;</code>  — cancelar tarea\n\n"

        "<b>── Publicar web ─────────────────</b>\n"
        "<code>/server</code>  — publicar dir actual vía Cloudflare\n"
        "<code>/server proxy &lt;puerto&gt;</code>  — tunel a app ya activa\n"
        "<code>/server fullstack</code>  — frontend + backend\n"
        "<code>/server status</code>  — estado del túnel\n"
        "<code>/server stop</code>  — parar túnel\n\n"

        "<b>── Control del bot ──────────────</b>\n"
        "<code>/status</code>  — estado de sesión y directorio\n"
        "<code>/bot stop</code>  — apagar el bot\n\n"

        "<b>── Entrada multimedia ───────────</b>\n"
        "🎤 <i>Audio</i>  — se transcribe y guarda como prompt\n"
        "🖼 <i>Imagen</i>  — se guarda en <code>images/</code>; con caption ejecuta directo\n"
        "   Usa <code>&lt;image&gt;</code> en el prompt para referenciar la última imagen",
        parse_mode="HTML",
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
        adapter = TelegramSendAdapter.from_context(context, chat_id)
        await run_task(chat_id, prompt, adapter)
        return

    engine = (state.get("ai_engine") or AI_ENGINE).upper()
    ai_model = state.get("ai_model") or ""
    model_hint = f" · <code>{html.escape(ai_model)}</code>" if ai_model else ""
    session_icon = "🟢 ACTIVO" if state["session_active"] else "⚪ INACTIVO"
    has_ctx = "sí (/reset para limpiar)" if state.get("session_id") else "no"
    pending = "sí" if state.get("pending_prompt") else "no"
    pending_images = len(state.get("pending_images") or [])
    intake = "activo" if state.get("prompt_intake_active") else "—"
    at_mode = "activo" if state.get("at_mode") else "—"
    branch_lock = state.get("branch_lock")
    branch_repo = state.get("branch_repo")
    if branch_lock and branch_repo:
        branch_line = f"<code>{html.escape(branch_lock)}</code> en <code>{html.escape(branch_repo)}</code>"
    else:
        branch_line = "—"
    at_draft = state.get("at_draft")
    at_info = ""
    if at_draft:
        from datetime import datetime
        run_at = datetime.fromisoformat(at_draft["run_at"])
        at_info = (
            f"\n<b>Borrador /at:</b> {run_at.strftime('%d/%m %H:%M')} "
            f"({len(at_draft.get('parts', []))} partes)"
        )
    await update.effective_message.reply_text(
        f"<b>Estado</b>  <i>[{engine}{model_hint}]</i>\n\n"
        f"<b>Sesión:</b> {session_icon}\n"
        f"<b>Dir base:</b> <code>{html.escape(state['base_dir'])}</code>\n"
        f"<b>Dir actual:</b> <code>{html.escape(state['cwd'])}</code>\n"
        f"<b>Contexto resumible:</b> {has_ctx}\n"
        f"<b>Prompt pendiente:</b> {pending}\n"
        f"<b>Imágenes pendientes:</b> {pending_images}\n"
        f"<b>Modo intake:</b> {intake}\n"
        f"<b>Modo acumulación:</b> {at_mode}{at_info}\n"
        f"<b>Rama bloqueada:</b> {branch_line}",
        parse_mode="HTML",
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


async def _cmd_run_with_engine(
    engine: str,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Shared execution logic for /claude and /codex."""
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state["session_active"]:
        await update.effective_message.reply_text(
            f"<b>{engine.upper()}</b> ya está en ejecución. Usa /stop primero.",
            parse_mode="HTML",
        )
        return

    # Set engine for this run (and remember it as the active engine)
    state["ai_engine"] = engine
    # Codex does not support session resuming
    if engine == "codex":
        state["session_id"] = None
        state["inject_resume_next"] = False

    label = "Codex" if engine == "codex" else "Claude Code"
    explicit_prompt = " ".join(context.args).strip() if context.args else ""

    if not explicit_prompt:
        state["claude_mode"] = True
        if engine == "claude":
            state["inject_resume_next"] = bool(state.get("session_id"))
        clear_prompt_intake(state)
        ctx_hint = ""
        if engine == "claude" and state.get("session_id"):
            ctx_hint = "\n<i>Contexto previo activo (--resume). Inyectaré /resume en tu próximo mensaje.</i>"
        await update.effective_message.reply_text(
            f"<b>Modo {label}</b> activo en <code>{html.escape(state['cwd'])}</code>{ctx_hint}\n"
            "Cada mensaje va directamente al motor de IA.\n"
            "<code>/exit</code> para salir.",
            parse_mode="HTML",
        )
        return

    state["claude_mode"] = True
    if engine == "claude":
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
        state["pending_confirm"] = explicit_prompt
        await update.effective_message.reply_text(
            "Patrón bloqueado detectado.\n"
            f"Match: <code>{html.escape(matched)}</code>\n"
            "Responde <b>YES</b> para ejecutar de todas formas.",
            parse_mode="HTML",
        )
        return

    prompt = maybe_inject_resume_prompt(state, explicit_prompt)
    audit(chat_id, f"PROMPT [{engine.upper()}]: {prompt}")
    adapter = TelegramSendAdapter.from_context(context, chat_id)
    await run_task(chat_id, prompt, adapter)


async def cmd_claude(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await _cmd_run_with_engine("claude", update, context)


async def cmd_codex(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    await _cmd_run_with_engine("codex", update, context)


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




async def cmd_bash(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    if state["session_active"]:
        await update.effective_message.reply_text("Claude is running. Use /stop first.")
        return

    if not context.args:
        await update.effective_message.reply_text("Usage: /bash <command>")
        return

    cmd_str = " ".join(context.args)
    matched = blocked_match(cmd_str)
    if matched is not None:
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
    _adapter = TelegramSendAdapter.from_context(context, chat_id)
    state["output_task"] = loop.create_task(output_reader(chat_id, _adapter))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, _adapter))


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


async def _finalize_at_draft(update: Update, state: dict, chat_id: int, *, auto: bool = False) -> bool:
    """Save the accumulated draft as a scheduled task. Returns True if a task was saved."""
    from companion.core.scheduler import add_task
    draft = state.get("at_draft")
    if not draft or not draft.get("parts"):
        state["at_mode"] = False
        state["at_draft"] = None
        if not auto:
            await update.effective_message.reply_text(
                "No hay contenido acumulado. Envía mensajes o audios antes de /at done."
            )
        return False

    from datetime import datetime
    prompt = "\n\n".join(draft["parts"])
    task_type = draft.get("task_type", "claude")
    cwd = draft["cwd"]
    run_at = datetime.fromisoformat(draft["run_at"])
    label = draft.get("label") or ""

    task_id = add_task(chat_id, cwd, run_at, task_type, prompt, label)
    state["at_mode"] = False
    state["at_draft"] = None

    time_str = run_at.strftime("%d/%m/%Y %H:%M")
    prefix = "Auto-guardado: " if auto else ""
    await update.effective_message.reply_text(
        f"{prefix}Tarea guardada [{task_id}]\n"
        f"Hora: {time_str}\n"
        f"Tipo: {task_type} | Dir: {cwd}\n"
        f"Partes: {len(draft['parts'])} | Prompt ({len(prompt)} chars):\n{prompt[:400]}"
    )
    return True


async def cmd_at(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    from companion.core.scheduler import add_task, parse_at_args, parse_at_time

    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    args = context.args or []

    # ── /at done / /at fin → finalizar borrador acumulado ─────────────────
    if args and args[0].lower() in ("done", "end", "fin", "ok", "listo", "confirmar", "save"):
        await _finalize_at_draft(update, state, chat_id)
        return

    # ── /at (sin args) → mostrar estado del modo acumulación ──────────────
    if not args:
        draft = state.get("at_draft")
        if state.get("at_mode") and draft:
            from datetime import datetime
            run_at = datetime.fromisoformat(draft["run_at"])
            parts_preview = "\n---\n".join(p[:100] for p in draft.get("parts", []))
            await update.effective_message.reply_text(
                f"Modo acumulación activo para las {run_at.strftime('%d/%m %H:%M')}\n"
                f"Directorio: {draft['cwd']}\n"
                f"Partes acumuladas: {len(draft.get('parts', []))}\n\n"
                f"{parts_preview or '(vacío)'}\n\n"
                "Envía mensajes/audios para seguir acumulando.\n"
                "/at done para guardar | /at HH:MM para nueva hora"
            )
        else:
            await update.effective_message.reply_text(
                "Uso:\n"
                "  /at HH:MM <prompt>           → programar ahora\n"
                "  /at HH:MM                    → modo acumulación (mensajes + audios)\n"
                "  /at HH:MM DD/MM <prompt>     → programar en fecha específica\n"
                "  /at 22:00 /bash git pull      → comando bash\n"
                "  /at done                      → guardar borrador acumulado\n"
                "  /scheduled                    → ver tareas pendientes\n\n"
                "Flujo multi-repo:\n"
                "  1. /cd proyecto_A → /at 14:00 → envía mensajes\n"
                "  2. /at done       → guarda tarea A\n"
                "  3. /cd proyecto_B → /at 15:00 → envía mensajes\n"
                "  4. /at done       → guarda tarea B\n"
                "  (Cada proyecto ejecuta en su propio proceso independiente)"
            )
        return

    # ── Parsear hora (+fecha opcional) ────────────────────────────────────
    run_at, prompt, error = parse_at_args(args)
    if error:
        await update.effective_message.reply_text(
            f"{error}\n\n"
            "Ejemplos:\n"
            "  /at 14:30 implementa el login con OAuth\n"
            "  /at 09:00 20/03 refactoriza el módulo de pagos\n"
            "  /at 22:00 /bash git pull && git status\n"
            "  /at 14:30  (sin prompt → modo acumulación)"
        )
        return

    assert run_at is not None

    # ── Sin prompt → entrar en modo acumulación ────────────────────────────
    if not prompt:
        # Si hay un borrador existente con contenido, guardarlo primero
        if state.get("at_mode") and state.get("at_draft") and state["at_draft"].get("parts"):
            await _finalize_at_draft(update, state, chat_id, auto=True)

        state["at_mode"] = True
        state["at_draft"] = {
            "run_at": run_at.isoformat(),
            "cwd": state["cwd"],
            "parts": [],
            "task_type": "claude",
            "label": "",
        }
        time_str = run_at.strftime("%d/%m/%Y %H:%M")
        await update.effective_message.reply_text(
            f"Modo acumulación activado para las {time_str}\n"
            f"Directorio: {state['cwd']}\n\n"
            "Ahora envía los mensajes o audios que quieras acumular.\n"
            "Envía /bash al inicio del mensaje para marcar como comando shell.\n"
            "Usa /at done cuando termines.\n"
            "Cambia de proyecto con /cd <path> y abre otro /at <hora> para otra tarea."
        )
        return

    # ── Con prompt → programar directamente ───────────────────────────────
    if prompt.startswith("/bash "):
        task_type = "bash"
        prompt = prompt[len("/bash "):].strip()
    else:
        task_type = "claude"

    task_id = add_task(chat_id, state["cwd"], run_at, task_type, prompt)
    time_str = run_at.strftime("%d/%m/%Y %H:%M")
    await update.effective_message.reply_text(
        f"Tarea programada [{task_id}]\n"
        f"Hora: {time_str}\n"
        f"Tipo: {task_type} | Dir: {state['cwd']}\n"
        f"Prompt: {prompt[:300]}"
    )


async def cmd_scheduled(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    del context
    if not is_authorized(update):
        return
    from companion.core.scheduler import list_tasks

    chat_id = update.effective_chat.id
    tasks = list_tasks(chat_id)
    if not tasks:
        await update.effective_message.reply_text(
            "No hay tareas programadas.\nUsa /at HH:MM <prompt> para programar una."
        )
        return

    lines = []
    for t in tasks:
        from datetime import datetime
        run_at = datetime.fromisoformat(t["run_at"])
        time_str = run_at.strftime("%d/%m/%Y %H:%M")
        preview = t["prompt"][:80] + ("..." if len(t["prompt"]) > 80 else "")
        lines.append(f"[{t['id']}] {time_str} ({t['type']})\n  {preview}")

    await update.effective_message.reply_text(
        f"Tareas programadas ({len(tasks)}):\n\n" + "\n\n".join(lines) +
        "\n\nUsa /unschedule <id> para cancelar."
    )


async def cmd_unschedule(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    from companion.core.scheduler import cancel_task

    if not context.args:
        await update.effective_message.reply_text(
            "Uso: /unschedule <id>\nUsa /scheduled para ver los IDs."
        )
        return

    chat_id = update.effective_chat.id
    task_id = context.args[0].strip()
    removed = cancel_task(chat_id, task_id)
    if removed:
        await update.effective_message.reply_text(f"Tarea [{task_id}] cancelada.")
    else:
        await update.effective_message.reply_text(
            f"No se encontró la tarea [{task_id}].\nUsa /scheduled para ver las tareas activas."
        )


async def cmd_engine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Switch AI engine per-chat: /engine · /engine claude · /engine codex [model]"""
    if not is_authorized(update):
        return
    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    args = context.args or []

    current_engine = state.get("ai_engine", "claude")
    current_model = state.get("ai_model", "") or ""

    # /engine — show current engine with switch buttons
    if not args:
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        model_hint = f" · <code>{html.escape(current_model)}</code>" if current_model else ""
        keyboard = [
            [
                InlineKeyboardButton(
                    "✅ Claude Code" if current_engine == "claude" else "Claude Code",
                    callback_data="engine:claude",
                ),
                InlineKeyboardButton(
                    "✅ Codex" if current_engine == "codex" else "Codex",
                    callback_data="engine:codex",
                ),
            ]
        ]
        await update.effective_message.reply_text(
            f"<b>Motor de IA activo:</b> <code>{current_engine.upper()}</code>{model_hint}\n\n"
            "Pulsa para cambiar o usa:\n"
            "<code>/engine claude</code>\n"
            "<code>/engine codex</code>\n"
            "<code>/engine codex o4-mini</code>  — con modelo específico",
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )
        return

    new_engine = args[0].lower()
    if new_engine not in ("claude", "codex"):
        await update.effective_message.reply_text(
            "<b>Motor no reconocido.</b> Usa <code>claude</code> o <code>codex</code>.\n\n"
            "Ejemplos:\n"
            "<code>/engine claude</code>\n"
            "<code>/engine codex</code>\n"
            "<code>/engine codex o4-mini</code>",
            parse_mode="HTML",
        )
        return

    new_model = args[1] if len(args) > 1 else ""

    if state.get("session_active"):
        await update.effective_message.reply_text(
            "<b>Hay una sesión activa.</b> Usa /stop o /reset antes de cambiar el motor.",
            parse_mode="HTML",
        )
        return

    state["ai_engine"] = new_engine
    state["ai_model"] = new_model
    # Codex sessions are not resumable — clear context when switching away from claude
    if new_engine == "codex":
        state["session_id"] = None
        state["inject_resume_next"] = False

    model_hint = f" · modelo <code>{html.escape(new_model)}</code>" if new_model else ""
    await update.effective_message.reply_text(
        f"<b>Motor cambiado a {new_engine.upper()}</b>{model_hint}\n"
        "<i>El cambio aplica a la próxima ejecución.</i>",
        parse_mode="HTML",
    )


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
    "cmd_paths",
    "cmd_projects",
    "cmd_save",
    "cmd_branch",
    "cmd_claude",
    "cmd_codex",
    "cmd_exit",
    "cmd_bash",
    "cmd_stop",
    "cmd_reset",
    "cmd_bot",
    "cmd_at",
    "cmd_engine",
    "cmd_scheduled",
    "cmd_unschedule",
    "_finalize_at_draft",
    "run_task",
]
