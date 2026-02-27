"""Audio transcription helpers (local faster-whisper)."""

from __future__ import annotations

import asyncio
import os
import tempfile
import threading
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

from companion.core.config import (
    WHISPER_BEAM_SIZE,
    WHISPER_ALLOW_AUTO_FALLBACK,
    WHISPER_COMPUTE_TYPE,
    WHISPER_DEVICE,
    WHISPER_MODEL,
    WHISPER_PRIMARY_LANGUAGE,
    WHISPER_VAD_FILTER,
)

try:
    from faster_whisper import WhisperModel
except Exception:  # pragma: no cover - optional runtime dependency
    WhisperModel = None

_WHISPER_MODEL = None
_WHISPER_LOCK = threading.Lock()


def _get_whisper_model():
    global _WHISPER_MODEL
    if WhisperModel is None:
        raise RuntimeError(
            "Local transcription is unavailable: install `faster-whisper`."
        )
    if _WHISPER_MODEL is not None:
        return _WHISPER_MODEL
    with _WHISPER_LOCK:
        if _WHISPER_MODEL is None:
            _WHISPER_MODEL = WhisperModel(
                WHISPER_MODEL,
                device=WHISPER_DEVICE,
                compute_type=WHISPER_COMPUTE_TYPE,
            )
    return _WHISPER_MODEL


def _transcribe_local_file(audio_path: str) -> str:
    model = _get_whisper_model()
    primary_language = (WHISPER_PRIMARY_LANGUAGE or "").strip().lower() or None
    run_kwargs = {
        "beam_size": WHISPER_BEAM_SIZE,
        "vad_filter": WHISPER_VAD_FILTER,
    }

    def _transcribe_once(language: str | None) -> str:
        kwargs = dict(run_kwargs)
        if language:
            kwargs["language"] = language
        segments, _info = model.transcribe(audio_path, **kwargs)
        chunks: list[str] = []
        for segment in segments:
            text = (getattr(segment, "text", "") or "").strip()
            if text:
                chunks.append(text)
        transcript = " ".join(chunks).strip()
        if not transcript:
            raise RuntimeError("Audio transcription returned no text.")
        return transcript

    primary_error: Exception | None = None
    if primary_language:
        try:
            return _transcribe_once(primary_language)
        except Exception as exc:
            primary_error = exc
            if not WHISPER_ALLOW_AUTO_FALLBACK:
                raise

    if primary_language and WHISPER_ALLOW_AUTO_FALLBACK:
        try:
            return _transcribe_once(None)
        except Exception as exc:
            if primary_error is not None:
                raise RuntimeError(
                    f"Primary language '{primary_language}' and auto fallback failed: "
                    f"{primary_error}; {exc}"
                ) from exc
            raise

    return _transcribe_once(None)


async def transcribe_telegram_audio(update: Update, context: ContextTypes.DEFAULT_TYPE) -> str:
    msg = update.effective_message
    if msg is None:
        raise RuntimeError("Message not available.")

    media = msg.voice or msg.audio
    if media is None:
        raise RuntimeError("No voice/audio payload found.")

    tg_file = await context.bot.get_file(media.file_id)
    suffix = ".ogg"
    if msg.audio and getattr(msg.audio, "file_name", None):
        suffix = Path(msg.audio.file_name).suffix or ".mp3"

    fd, local_path = tempfile.mkstemp(suffix=suffix)
    os.close(fd)

    try:
        await tg_file.download_to_drive(custom_path=local_path)
        return await asyncio.to_thread(_transcribe_local_file, local_path)
    except Exception as exc:
        raise RuntimeError(f"Local transcription failed: {exc}") from exc
    finally:
        try:
            os.remove(local_path)
        except Exception:
            pass
