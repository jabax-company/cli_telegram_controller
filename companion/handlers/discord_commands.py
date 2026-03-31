"""Discord slash-command handlers.

All commands mirror their Telegram counterparts in commands.py but use the
discord.py Interaction API.  Business logic (state, path resolution, etc.) is
shared with the Telegram handlers via the same companion.core modules.
"""

from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from companion.core.claude_runtime import keepalive, output_reader, run_task
from companion.core.config import AI_ENGINE, DISCORD_USER_ID
from companion.core.paths import resolve_path
from companion.core.prompt_optimizer import clear_prompt_intake
from companion.core.security import blocked_match
from companion.core.send_adapter import DiscordSendAdapter
from companion.core.state import get_state, maybe_inject_resume_prompt, reset_chat_state
from companion.core.storage import audit, load_projects, save_projects


# ── Auth helper ───────────────────────────────────────────────────────────────


def _is_authorized(interaction) -> bool:
    return interaction.user.id == DISCORD_USER_ID


# ── Git helpers (shared logic) ────────────────────────────────────────────────


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
        return int(completed.returncode), completed.stdout.strip(), completed.stderr.strip()

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
    rc, repo_now, _ = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
    if rc != 0:
        return False, "Branch lock active but current folder is not a Git repo. Use /cd or /branch off."
    repo_now_resolved = str(Path(repo_now).resolve())
    if repo_now_resolved != locked_repo:
        return False, f"Branch lock belongs to another repo.\nLocked: {locked_repo}\nCurrent: {repo_now_resolved}"
    rc, current_branch, err = await _run_git(state["cwd"], "branch", "--show-current")
    if rc != 0:
        return False, f"Could not read current branch: {err or current_branch}"
    if current_branch == locked_branch:
        return True, None
    rc, out, err = await _switch_branch(state["cwd"], locked_branch, create=False)
    if rc != 0:
        return False, f"Could not switch to locked branch '{locked_branch}': {err or out}"
    return True, f"Auto-switched to locked branch: {locked_branch}"


# ── Respond helper ────────────────────────────────────────────────────────────


async def _reply(interaction, text: str, ephemeral: bool = False) -> None:
    """Send a response, handling the deferred/not-deferred cases."""
    if interaction.response.is_done():
        await interaction.followup.send(text[:2000], ephemeral=ephemeral)
    else:
        await interaction.response.send_message(text[:2000], ephemeral=ephemeral)


async def _defer(interaction) -> None:
    """Defer the interaction (show "thinking...") if not already done."""
    if not interaction.response.is_done():
        await interaction.response.defer()


# ── Command implementations ───────────────────────────────────────────────────


async def cmd_start(interaction) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)
    engine = (state.get("ai_engine") or AI_ENGINE).upper()
    await _reply(
        interaction,
        f"**AI Code Companion** [{engine}]\n\n"
        f"**Dir actual:** `{state['cwd']}`\n\n"
        "**Flujo rápido:**\n"
        "1. Usa `/cd <ruta>` para cambiar de directorio\n"
        "2. Usa `/claude <prompt>` para ejecutar con Claude Code\n"
        "3. Texto enviado en modo Claude va directo al motor\n\n"
        "**Comandos esenciales:**\n"
        "`/cd <ruta>` — cambiar directorio\n"
        "`/claude <prompt>` — ejecutar con Claude Code\n"
        "`/codex <prompt>` — ejecutar con Codex\n"
        "`/bash <cmd>` — comando shell\n"
        "`/branch <nombre>` — rama Git y bloqueo\n"
        "`/server` — publicar web vía Cloudflare\n\n"
        "`/help` para la referencia completa.",
    )


async def cmd_help(interaction) -> None:
    if not _is_authorized(interaction):
        return
    state = get_state(interaction.channel_id)
    engine = AI_ENGINE.upper()
    await _reply(
        interaction,
        f"**Referencia de comandos** [Motor: {engine}]\n\n"
        f"**Dir base:** `{state['base_dir']}`\n"
        f"**Dir actual:** `{state['cwd']}`\n\n"
        "**── Navegación ──**\n"
        "`/cd` — ir al dir base\n"
        "`/cd <ruta>` — cambiar directorio\n"
        "`/status` — estado de sesión\n\n"
        "**── Ejecución IA ──**\n"
        "`/claude <prompt>` — ejecutar con Claude Code\n"
        "`/codex <prompt>` — ejecutar con Codex\n"
        "`/bash <cmd>` — comando shell\n"
        "`/branch <nombre>` — crear/cambiar rama Git\n"
        "`/branch off` — quitar bloqueo de rama\n"
        "`/exit` — salir del modo Claude\n"
        "`/stop` — interrumpir ejecución\n"
        "`/reset` — borrar proceso y contexto\n\n"
        "**── Publicar web ──**\n"
        "`/server` — publicar directorio actual\n"
        "`/server proxy <puerto>` — túnel a app activa\n"
        "`/server stop` — parar túnel\n\n"
        "ℹ️ Envía texto normal en un canal cuando el modo Claude está activo y va directo al motor.",
    )


async def cmd_status(interaction) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)
    engine = (state.get("ai_engine") or AI_ENGINE).upper()
    ai_model = state.get("ai_model") or ""
    model_hint = f" · `{ai_model}`" if ai_model else ""
    session_icon = "🟢 ACTIVO" if state["session_active"] else "⚪ INACTIVO"
    has_ctx = "sí (/reset para limpiar)" if state.get("session_id") else "no"
    pending = "sí" if state.get("pending_prompt") else "no"
    branch_lock = state.get("branch_lock")
    branch_repo = state.get("branch_repo")
    branch_line = f"`{branch_lock}` en `{branch_repo}`" if branch_lock and branch_repo else "—"
    await _reply(
        interaction,
        f"**Estado** [{engine}{model_hint}]\n\n"
        f"**Sesión:** {session_icon}\n"
        f"**Dir base:** `{state['base_dir']}`\n"
        f"**Dir actual:** `{state['cwd']}`\n"
        f"**Contexto resumible:** {has_ctx}\n"
        f"**Prompt pendiente:** {pending}\n"
        f"**Rama bloqueada:** {branch_line}",
    )


async def cmd_cd(interaction, path: str) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)
    if not path.strip():
        state["cwd"] = str(Path(state["base_dir"]).resolve())
        await _reply(interaction, f"Now in: `{state['cwd']}`")
        return
    target = resolve_path(path.strip(), current_dir=state["cwd"], base_dir=state["base_dir"])
    if target is None:
        await _reply(interaction, f"Directory not found: {path}")
        return
    state["cwd"] = target
    await _reply(interaction, f"Now in: `{target}`")


async def cmd_branch(interaction, branch_name: str) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)

    if state["session_active"]:
        await _reply(interaction, "Claude is running. Use `/stop` first.")
        return

    if not branch_name.strip():
        rc, repo_root, _ = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
        if rc != 0:
            await _reply(interaction, "Current directory is not a Git repo.")
            return
        rc, current_branch, _ = await _run_git(state["cwd"], "branch", "--show-current")
        locked = state.get("branch_lock")
        if not locked:
            state["branch_lock"] = current_branch
            state["branch_repo"] = str(Path(repo_root).resolve())
        await _reply(
            interaction,
            f"Repo: `{Path(repo_root).resolve()}`\n"
            f"Branch actual: `{current_branch}`\n"
            f"Branch lock: `{state.get('branch_lock') or 'none'}`",
        )
        return

    if branch_name.lower() in {"off", "unlock", "none", "reset"}:
        state["branch_lock"] = None
        state["branch_repo"] = None
        await _reply(interaction, "Branch lock cleared.")
        return

    rc, repo_root, _ = await _run_git(state["cwd"], "rev-parse", "--show-toplevel")
    if rc != 0:
        await _reply(interaction, "Current directory is not a Git repo.")
        return

    rc, _, _ = await _run_git(state["cwd"], "show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}")
    exists = rc == 0
    rc, out, err = await _switch_branch(state["cwd"], branch_name, create=not exists)
    if rc != 0:
        await _reply(interaction, f"Could not switch/create branch '{branch_name}':\n{err or out}")
        return

    rc, current_branch, _ = await _run_git(state["cwd"], "branch", "--show-current")
    state["branch_lock"] = current_branch
    state["branch_repo"] = str(Path(repo_root).resolve())
    action = "Switched to" if exists else "Created"
    await _reply(interaction, f"{action} branch: `{current_branch}`\nBranch lock enabled.")


async def _cmd_run_with_engine(engine: str, interaction, prompt: str) -> None:
    chat_id = interaction.channel_id
    state = get_state(chat_id)

    if state["session_active"]:
        await _reply(interaction, f"**{engine.upper()}** ya está en ejecución. Usa `/stop` primero.")
        return

    state["ai_engine"] = engine
    if engine == "codex":
        state["session_id"] = None
        state["inject_resume_next"] = False

    label = "Codex" if engine == "codex" else "Claude Code"
    explicit_prompt = prompt.strip()

    if not explicit_prompt:
        state["claude_mode"] = True
        if engine == "claude":
            state["inject_resume_next"] = bool(state.get("session_id"))
        clear_prompt_intake(state)
        ctx_hint = ""
        if engine == "claude" and state.get("session_id"):
            ctx_hint = "\n*Contexto previo activo. Inyectaré /resume en tu próximo mensaje.*"
        await _reply(
            interaction,
            f"**Modo {label}** activo en `{state['cwd']}`{ctx_hint}\n"
            "Cada mensaje va directo al motor de IA.\n"
            "`/exit` para salir.",
        )
        return

    state["claude_mode"] = True
    if engine == "claude":
        state["inject_resume_next"] = bool(state.get("session_id"))
    clear_prompt_intake(state)

    ok, msg = await _ensure_branch_lock(state)
    if not ok:
        await _reply(interaction, msg or "Branch lock validation failed.")
        return
    if msg:
        await _reply(interaction, msg)

    matched = blocked_match(explicit_prompt)
    if matched is not None:
        state["pending_confirm"] = explicit_prompt
        await _reply(
            interaction,
            f"Patrón bloqueado detectado.\nMatch: `{matched}`\n"
            "Responde **YES** para ejecutar de todas formas.",
        )
        return

    await _defer(interaction)
    final_prompt = maybe_inject_resume_prompt(state, explicit_prompt)
    audit(chat_id, f"DISCORD PROMPT [{engine.upper()}]: {final_prompt}")
    adapter = DiscordSendAdapter(interaction.channel, chat_id)
    await run_task(chat_id, final_prompt, adapter)


async def cmd_claude(interaction, prompt: str = "") -> None:
    if not _is_authorized(interaction):
        return
    await _cmd_run_with_engine("claude", interaction, prompt)


async def cmd_codex(interaction, prompt: str = "") -> None:
    if not _is_authorized(interaction):
        return
    await _cmd_run_with_engine("codex", interaction, prompt)


async def cmd_exit(interaction) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)
    if not state.get("claude_mode"):
        await _reply(interaction, "Not in Claude mode.")
        return
    state["claude_mode"] = False
    ctx_hint = " Context preserved — `/claude` to resume." if state.get("session_id") else ""
    await _reply(interaction, f"Exited Claude mode.{ctx_hint}")


async def cmd_bash(interaction, command: str) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)

    if state["session_active"]:
        await _reply(interaction, "Claude is running. Use `/stop` first.")
        return

    matched = blocked_match(command)
    if matched is not None:
        await _reply(interaction, f"Blocked pattern detected: `{matched}`")
        return

    audit(chat_id, f"DISCORD BASH: {command}")
    await _defer(interaction)
    await interaction.followup.send(f"$ {command}")

    proc = await asyncio.create_subprocess_shell(
        command,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        cwd=state["cwd"],
    )
    state["proc"] = proc
    state["session_active"] = True
    loop = asyncio.get_running_loop()
    adapter = DiscordSendAdapter(interaction.channel, chat_id)
    state["output_task"] = loop.create_task(output_reader(chat_id, adapter))
    state["keepalive_task"] = loop.create_task(keepalive(chat_id, adapter))


async def cmd_stop(interaction) -> None:
    if not _is_authorized(interaction):
        return
    state = get_state(interaction.channel_id)
    if not (state["session_active"] and state["proc"]):
        await _reply(interaction, "No active session to stop.")
        return
    try:
        state["proc"].terminate()
        await _reply(interaction, "Sent terminate signal.")
    except Exception as e:
        await _reply(interaction, f"Error: {e}")


async def cmd_reset(interaction) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
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
    await _reply(interaction, "Session reset. Context cleared.")


async def cmd_save(interaction, name: str) -> None:
    if not _is_authorized(interaction):
        return
    if not name.strip():
        await _reply(interaction, "Usage: `/save <name>`")
        return
    state = get_state(interaction.channel_id)
    projects = load_projects()
    projects[name.strip()] = state["cwd"]
    save_projects(projects)
    await _reply(interaction, f"Saved `{name.strip()}` → `{state['cwd']}`")


async def cmd_projects(interaction) -> None:
    if not _is_authorized(interaction):
        return
    projects = load_projects()
    if not projects:
        await _reply(interaction, "No saved projects yet.\nUse `/save <name>` to save current directory.")
        return
    lines = "\n".join(f"- `{name}` → `{path}`" for name, path in projects.items())
    await _reply(interaction, f"**Saved projects:**\n{lines}")


async def cmd_engine(interaction, engine: str) -> None:
    if not _is_authorized(interaction):
        return
    chat_id = interaction.channel_id
    state = get_state(chat_id)
    engine = engine.strip().lower()
    valid = {"claude", "codex", "claude-channel"}
    if engine not in valid:
        await _reply(interaction, f"Unknown engine. Valid: {', '.join(sorted(valid))}")
        return
    state["ai_engine"] = engine
    await _reply(interaction, f"Engine set to: **{engine}**")
