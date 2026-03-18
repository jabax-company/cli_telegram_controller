"""In-memory state for chats and serving sessions."""

from __future__ import annotations

import time

from companion.core.config import AI_ENGINE, BASE_DIR, CODEX_MODEL, INITIAL_DIR

sessions: dict[int, dict] = {}
serve_sessions: dict[int, dict] = {}


def blank_state() -> dict:
    return {
        "cwd": INITIAL_DIR,
        "base_dir": str(BASE_DIR),
        "branch_lock": None,
        "branch_repo": None,
        "prompt_intake_active": False,
        "prompt_draft": None,
        "prompt_answers": [],
        "prompt_question_ids": [],
        "prompt_q_index": 0,
        "claude_mode": False,
        "session_active": False,
        "proc": None,
        "output_task": None,
        "keepalive_task": None,
        "pending_confirm": None,
        "session_id": None,
        "inject_resume_next": False,
        "pending_prompt": None,
        "pending_images": [],
        "image_history": [],
        "browse_root": None,
        "browse_path": None,
        "browse_page": 0,
        "last_interaction": time.monotonic(),
        # scheduling accumulation mode
        "at_mode": False,
        "at_draft": None,  # {"run_at": str, "cwd": str, "parts": [], "task_type": str}
        # AI engine (per-chat, overrides global AI_ENGINE env var)
        "ai_engine": AI_ENGINE,   # "claude" or "codex"
        "ai_model": CODEX_MODEL,  # model override (mainly for codex)
    }


def _ensure_state_shape(state: dict) -> None:
    state.setdefault("inject_resume_next", False)
    state.setdefault("pending_images", [])
    state.setdefault("image_history", [])
    state.setdefault("last_interaction", time.monotonic())
    state.setdefault("at_mode", False)
    state.setdefault("at_draft", None)
    state.setdefault("ai_engine", AI_ENGINE)
    state.setdefault("ai_model", CODEX_MODEL)


def get_state(chat_id: int) -> dict:
    if chat_id not in sessions:
        sessions[chat_id] = blank_state()
    state = sessions[chat_id]
    _ensure_state_shape(state)
    return state


def reset_chat_state(chat_id: int, cwd: str | None = None) -> dict:
    sessions[chat_id] = blank_state()
    if cwd is not None:
        sessions[chat_id]["cwd"] = cwd
    return sessions[chat_id]


def get_serve_state(chat_id: int) -> dict:
    if chat_id not in serve_sessions:
        serve_sessions[chat_id] = {
            "tunnel_proc": None,
            "app_proc": None,
            "app_output_task": None,
            "extra_procs": [],
            "url": None,
            "task": None,
            "mode": None,
            "target": None,
            "cwd": None,
        }
    state = serve_sessions[chat_id]
    state.setdefault("extra_procs", [])
    state.setdefault("app_output_task", None)
    return state


def is_serve_active(chat_id: int) -> bool:
    state = serve_sessions.get(chat_id)
    return bool(state and state["tunnel_proc"] is not None and state["tunnel_proc"].returncode is None)


def touch_activity(chat_id: int) -> None:
    state = get_state(chat_id)
    state["last_interaction"] = time.monotonic()


def known_chat_ids() -> set[int]:
    return set(sessions) | set(serve_sessions)


def maybe_inject_resume_prompt(state: dict, prompt: str) -> str:
    clean = (prompt or "").strip()
    if not clean:
        return prompt
    if state.get("inject_resume_next"):
        state["inject_resume_next"] = False
        return f"/resume\n{clean}"
    return prompt
