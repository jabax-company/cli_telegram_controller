"""Tests for output chunking, state helpers, and path resolution."""

from __future__ import annotations

from companion.core.claude_runtime import split_message
from companion.core.state import (
    blank_state,
    get_state,
    maybe_inject_resume_prompt,
    reset_chat_state,
)


def test_split_message_short_passthrough():
    assert split_message("hello") == ["hello"]


def test_split_message_splits_on_newlines():
    text = "\n".join(f"line {i}" for i in range(2000))
    pieces = split_message(text, max_len=500, max_chunks=8)
    assert all(len(p) <= 600 for p in pieces)  # allow for truncation suffix
    # No content lost mid-line at chunk boundaries
    assert pieces[0].endswith(tuple(f"line {i}" for i in range(2000)))


def test_split_message_caps_chunk_count():
    text = "x" * 10_000
    pieces = split_message(text, max_len=1000, max_chunks=3)
    assert len(pieces) == 3
    assert "truncated" in pieces[-1]


def test_blank_state_has_required_keys():
    state = blank_state()
    for key in ("cwd", "claude_mode", "session_active", "pending_confirm", "session_id"):
        assert key in state


def test_get_and_reset_state():
    state = get_state(999)
    state["claude_mode"] = True
    state["cwd"] = "/tmp"
    fresh = reset_chat_state(999, cwd="/tmp")
    assert fresh["claude_mode"] is False
    assert fresh["cwd"] == "/tmp"


def test_maybe_inject_resume_prompt():
    state = {"inject_resume_next": True}
    assert maybe_inject_resume_prompt(state, "do it") == "/resume\ndo it"
    assert state["inject_resume_next"] is False
    assert maybe_inject_resume_prompt(state, "do it") == "do it"
    assert maybe_inject_resume_prompt(state, "  ") == "  "
