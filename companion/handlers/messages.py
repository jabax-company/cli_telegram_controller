"""Message and audio handlers."""

from __future__ import annotations

import mimetypes
import re
from datetime import datetime, timezone
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.audio import transcribe_telegram_audio
from companion.core.auth import is_authorized
from companion.core.prompt_optimizer import (
    answered_question_count,
    build_optimized_prompt,
    enhance_prompt_with_ai,
    next_question_text,
    question_count_range,
    record_answer_and_advance,
)
from companion.core.claude_runtime import run_task
from companion.core.config import IMAGES_DIR, MAX_IMAGE_HISTORY, MAX_PENDING_IMAGES, PROMPT_ENHANCE_ENABLED, ROOT_DIR
from companion.core.security import blocked_match
from companion.core.server_runtime import cmd_server
from companion.core.state import get_state, maybe_inject_resume_prompt
from companion.core.storage import audit

_IMAGE_PLACEHOLDER_RE = re.compile(r"<\s*images?\s*>", flags=re.IGNORECASE)


async def _maybe_enhance(prompt: str, cwd: str, msg) -> str:
    """If PROMPT_ENHANCE_ENABLED, improve the prompt with AI and notify the user."""
    if not PROMPT_ENHANCE_ENABLED:
        return prompt
    notice = await msg.reply_text("✨ Mejorando prompt...")
    enhanced = await enhance_prompt_with_ai(prompt, cwd)
    preview = enhanced if len(enhanced) <= 600 else enhanced[:600] + "..."
    try:
        await notice.edit_text(f"✨ <b>Prompt mejorado:</b>\n\n{preview}", parse_mode="HTML")
    except Exception:
        pass
    return enhanced


def _normalize_image_extension(
    file_name: str | None,
    mime_type: str | None,
    remote_path: str | None,
) -> str:
    for candidate in (file_name, remote_path):
        if candidate:
            suffix = Path(candidate).suffix.lower()
            if suffix:
                return suffix
    if mime_type:
        guessed = mimetypes.guess_extension(mime_type, strict=False)
        if guessed:
            return guessed.lower()
    return ".jpg"


def _format_image_reference_block(images: list[dict]) -> str:
    lines = [
        "Use these local images as input context:",
    ]
    for idx, image in enumerate(images, start=1):
        lines.append(
            f"{idx}. {image['relative_path']} (absolute path: {image['path']})"
        )
    lines.append("Inspect these images before producing the final answer.")
    return "\n".join(lines)


def _prepare_prompt_with_images(state: dict, text: str) -> tuple[str, bool]:
    prompt = (text or "").strip()
    pending_images = list(state.get("pending_images") or [])
    image_history = list(state.get("image_history") or [])
    placeholder_present = bool(_IMAGE_PLACEHOLDER_RE.search(prompt))

    selected_images: list[dict] = []
    if pending_images:
        selected_images = pending_images
    elif placeholder_present and image_history:
        selected_images = [image_history[-1]]

    prompt_clean = _IMAGE_PLACEHOLDER_RE.sub("", prompt).strip()
    if not selected_images:
        return prompt, False

    prompt_block = _format_image_reference_block(selected_images)
    full_prompt = f"{prompt_clean}\n\n{prompt_block}" if prompt_clean else prompt_block

    if pending_images:
        state["pending_images"] = []

    return full_prompt, True


async def _save_incoming_image(update: Update, context: ContextTypes.DEFAULT_TYPE, state: dict) -> dict:
    msg = update.effective_message
    if msg is None:
        raise RuntimeError("Message not available.")

    media = None
    file_name = None
    mime_type = None
    if msg.photo:
        media = msg.photo[-1]
    elif msg.document:
        media = msg.document
        file_name = msg.document.file_name
        mime_type = msg.document.mime_type

    if media is None:
        raise RuntimeError("No image payload found.")

    tg_file = await context.bot.get_file(media.file_id)
    extension = _normalize_image_extension(
        file_name=file_name,
        mime_type=mime_type,
        remote_path=getattr(tg_file, "file_path", None),
    )
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    unique_id = getattr(media, "file_unique_id", None) or media.file_id
    safe_unique_id = re.sub(r"[^a-zA-Z0-9_-]", "_", str(unique_id))
    filename = f"{stamp}_{safe_unique_id}{extension}"

    IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    destination = (IMAGES_DIR / filename).resolve()
    await tg_file.download_to_drive(custom_path=str(destination))

    try:
        relative_path = str(destination.relative_to(ROOT_DIR))
    except Exception:
        relative_path = str(destination)

    image_entry = {
        "file_name": filename,
        "path": str(destination),
        "relative_path": relative_path,
    }
    image_history = state.setdefault("image_history", [])
    image_history.append(image_entry)
    if len(image_history) > MAX_IMAGE_HISTORY:
        state["image_history"] = image_history[-MAX_IMAGE_HISTORY:]

    pending_images = state.setdefault("pending_images", [])
    pending_images.append(image_entry)
    if len(pending_images) > MAX_PENDING_IMAGES:
        state["pending_images"] = pending_images[-MAX_PENDING_IMAGES:]

    return image_entry


async def _process_prompt_intake_text(state: dict, text: str, msg) -> None:
    if not state.get("prompt_intake_active"):
        await msg.reply_text(
            "No se guardo ningun prompt.\n"
            "Primero ejecuta /claude para activar el modo prompt."
        )
        return

    incoming = text.strip()
    if not incoming:
        await msg.reply_text("Prompt vacio. Envia texto con contenido.")
        return

    if state.get("prompt_draft") is None:
        state["prompt_draft"] = incoming
        state["prompt_answers"] = []
        state["prompt_q_index"] = 0
        q = next_question_text(state)
        q_min, q_max = question_count_range()
        await msg.reply_text(
            f"Perfecto. Vamos a optimizar tu prompt ({q_min} a {q_max} preguntas).\n\n"
            f"{q}"
        )
        return

    done = record_answer_and_advance(state, incoming)
    if not done:
        q = next_question_text(state)
        await msg.reply_text(q or "Continua.")
        return

    optimized = build_optimized_prompt(state, state["cwd"])
    count = answered_question_count(state)
    state["pending_prompt"] = optimized
    state["prompt_intake_active"] = False
    state["prompt_draft"] = None
    state["prompt_answers"] = []
    state["prompt_question_ids"] = []
    state["prompt_q_index"] = 0

    preview = optimized if len(optimized) <= 1400 else optimized[:1400] + "\n...\n(truncated)"
    await msg.reply_text(
        "Prompt optimizado listo y preparado para ejecutar.\n"
        f"Preguntas realizadas: {count}.\n"
        "Envia /claude para correrlo.\n\n"
        f"{preview}"
    )


def _parse_slash_text(text: str) -> tuple[str, list[str]] | None:
    stripped = text.strip()
    if not stripped.startswith("/"):
        return None
    first_line = stripped.splitlines()[0].strip()
    if not first_line:
        return None
    parts = first_line.split()
    if not parts:
        return None
    cmd = parts[0][1:]
    if not cmd:
        return None
    cmd = cmd.split("@", 1)[0].lower()
    return cmd, parts[1:]


async def _dispatch_unparsed_command(update: Update, context: ContextTypes.DEFAULT_TYPE, text: str) -> bool:
    parsed = _parse_slash_text(text)
    if parsed is None:
        return False

    cmd, args = parsed
    if cmd not in {"server", "serve"}:
        return False

    class _Ctx:
        def __init__(self, bot, parsed_args):
            self.bot = bot
            self.args = parsed_args

    await cmd_server(update, _Ctx(context.bot, args))
    return True


async def _add_to_at_draft(state: dict, text: str, msg, source: str = "texto") -> None:
    """Accumulate text into the at_draft and acknowledge."""
    draft = state.get("at_draft")
    if draft is None:
        state["at_mode"] = False
        await msg.reply_text("Error interno: borrador perdido. Usa /at HH:MM para reiniciar.")
        return

    # Detect /bash prefix inside accumulation
    if text.startswith("/bash "):
        draft["task_type"] = "bash"
        text = text[len("/bash "):].strip()

    draft["parts"].append(text)
    part_num = len(draft["parts"])

    from datetime import datetime
    run_at = datetime.fromisoformat(draft["run_at"])
    preview = text[:120] + ("..." if len(text) > 120 else "")
    await msg.reply_text(
        f"Parte {part_num} añadida ({source})\n"
        f"Hora programada: {run_at.strftime('%d/%m %H:%M')} | Dir: {draft['cwd']}\n"
        f"Preview: {preview}\n\n"
        "Sigue enviando mensajes/audios o /at done para guardar."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    msg = update.effective_message
    if msg is None:
        return

    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    text = (msg.text or "").strip()
    if not text:
        return

    # ── Scheduling accumulation mode (bypasses session_active) ────────────
    if state.get("at_mode"):
        await _add_to_at_draft(state, text, msg)
        return

    if state["pending_confirm"]:
        if text.upper() == "YES":
            prompt = state["pending_confirm"]
            state["pending_confirm"] = None
            prompt, _ = _prepare_prompt_with_images(state, prompt)
            prompt = maybe_inject_resume_prompt(state, prompt)
            audit(chat_id, f"CONFIRMED_BLOCKED: {prompt}")
            await run_task(chat_id, prompt, context, update)
        else:
            state["pending_confirm"] = None
            await msg.reply_text("Cancelled.")
        return

    if state["session_active"]:
        await msg.reply_text("Claude is working. Please wait.")
        return

    if await _dispatch_unparsed_command(update, context, text):
        return

    if state.get("claude_mode"):
        matched = blocked_match(text)
        if matched is not None:
            state["pending_confirm"] = text
            await msg.reply_text(
                "Blocked pattern detected.\n"
                f"Match: {matched}\n"
                "Reply YES to run anyway, or anything else to cancel."
            )
            return
        prompt, _ = _prepare_prompt_with_images(state, text)
        prompt = await _maybe_enhance(prompt, state["cwd"], msg)
        prompt = maybe_inject_resume_prompt(state, prompt)
        audit(chat_id, f"CLAUDE_MODE: {prompt}")
        await run_task(chat_id, prompt, context, update)
        return

    prompt_text = text
    if state.get("prompt_intake_active"):
        prompt_text, _ = _prepare_prompt_with_images(state, text)
    await _process_prompt_intake_text(state, prompt_text, msg)


async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    msg = update.effective_message
    if msg is None:
        return

    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    # ── Scheduling accumulation mode (bypasses session_active) ────────────
    if state.get("at_mode"):
        progress = await msg.reply_text("Transcribiendo audio para tarea programada...")
        try:
            text = await transcribe_telegram_audio(update, context)
        except Exception as e:
            await progress.edit_text(f"Error de transcripción: {e}")
            return
        preview_at = text[:300] + (f"\n…({len(text)} chars totales)" if len(text) > 300 else "")
        await progress.edit_text(f"Transcrito: {preview_at}")
        await _add_to_at_draft(state, text, msg, source="audio")
        return

    if state["pending_confirm"]:
        await msg.reply_text("Please reply YES or cancel first.")
        return
    if state["session_active"]:
        await msg.reply_text("Claude is working. Please wait.")
        return
    if not state.get("claude_mode") and not state.get("prompt_intake_active"):
        await msg.reply_text(
            "No se guardo ningun audio.\n"
            "Primero ejecuta /claude para activar el modo Claude."
        )
        return

    progress = await msg.reply_text("Transcribing audio...")
    try:
        text = await transcribe_telegram_audio(update, context)
    except Exception as e:
        await progress.edit_text(f"Audio transcription failed: {e}")
        return

    preview = text[:800] + (f"\n\n…({len(text)} chars totales, preview truncado)" if len(text) > 800 else "")
    await progress.edit_text(f"Transcribed:\n{preview}")

    if state.get("claude_mode"):
        matched = blocked_match(text)
        if matched is not None:
            state["pending_confirm"] = text
            await msg.reply_text(
                "Blocked pattern detected.\n"
                f"Match: {matched}\n"
                "Reply YES to run anyway, or anything else to cancel."
            )
            return
        prompt, _ = _prepare_prompt_with_images(state, text)
        prompt = await _maybe_enhance(prompt, state["cwd"], msg)
        prompt = maybe_inject_resume_prompt(state, prompt)
        audit(chat_id, f"CLAUDE_MODE_AUDIO: {prompt}")
        await run_task(chat_id, prompt, context, update)
        return

    prompt_text = text
    if state.get("prompt_intake_active"):
        prompt_text, _ = _prepare_prompt_with_images(state, text)
    await _process_prompt_intake_text(state, prompt_text, msg)


async def handle_image(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    msg = update.effective_message
    if msg is None:
        return

    chat_id = update.effective_chat.id
    state = get_state(chat_id)

    try:
        image_entry = await _save_incoming_image(update, context, state)
    except Exception as exc:
        await msg.reply_text(f"Image save failed: {exc}")
        return

    audit(chat_id, f"IMAGE_SAVED: {image_entry['relative_path']}")
    caption = (msg.caption or "").strip()

    if state["session_active"]:
        await msg.reply_text(
            f"Image saved: {image_entry['relative_path']}\n"
            "Claude is working now. I will include this image in your next prompt."
        )
        return

    if not caption:
        await msg.reply_text(
            f"Image saved: {image_entry['relative_path']}\n"
            "Send your next prompt and I will include this image."
        )
        return

    if state["pending_confirm"]:
        await msg.reply_text(
            f"Image saved: {image_entry['relative_path']}\n"
            "Please reply YES or cancel first. The image is queued for your next prompt."
        )
        return

    if state.get("claude_mode"):
        matched = blocked_match(caption)
        if matched is not None:
            state["pending_confirm"] = caption
            await msg.reply_text(
                "Blocked pattern detected.\n"
                f"Match: {matched}\n"
                "Reply YES to run anyway, or anything else to cancel."
            )
            return
        prompt, _ = _prepare_prompt_with_images(state, caption)
        prompt = await _maybe_enhance(prompt, state["cwd"], msg)
        prompt = maybe_inject_resume_prompt(state, prompt)
        audit(chat_id, f"CLAUDE_MODE_IMAGE: {prompt}")
        await run_task(chat_id, prompt, context, update)
        return

    if state.get("prompt_intake_active"):
        prompt, _ = _prepare_prompt_with_images(state, caption)
        await _process_prompt_intake_text(state, prompt, msg)
        return

    await msg.reply_text(
        f"Image saved: {image_entry['relative_path']}\n"
        "Caption received. Enable Claude mode with /claude and send your prompt."
    )


async def handle_command_passthrough(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return

    msg = update.effective_message
    if msg is None:
        return

    chat_id = update.effective_chat.id
    state = get_state(chat_id)
    if not state.get("claude_mode"):
        return
    if state.get("pending_confirm"):
        await msg.reply_text("Please reply YES or cancel first.")
        return
    if state.get("session_active"):
        await msg.reply_text("Claude is working. Please wait.")
        return

    text = (msg.text or "").strip()
    parsed = _parse_slash_text(text)
    if parsed is None:
        return
    cmd, args = parsed
    slash_prompt = f"/{cmd}"
    if args:
        slash_prompt += " " + " ".join(args)

    matched = blocked_match(slash_prompt)
    if matched is not None:
        state["pending_confirm"] = slash_prompt
        await msg.reply_text(
            "Blocked pattern detected.\n"
            f"Match: {matched}\n"
            "Reply YES to run anyway, or anything else to cancel."
        )
        return

    slash_prompt = maybe_inject_resume_prompt(state, slash_prompt)
    audit(chat_id, f"CLAUDE_MODE_COMMAND: {slash_prompt}")
    await run_task(chat_id, slash_prompt, context, update)
