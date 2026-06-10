"""Tests for message parsing helpers and the system module."""

from __future__ import annotations

import pytest

from companion.core.system import _fmt_bytes, get_system_info, list_processes
from companion.handlers.messages import (
    _parse_slash_text,
    _prepare_prompt_with_images,
    _sanitize_filename,
)


def test_parse_slash_text():
    assert _parse_slash_text("/server proxy 5000") == ("server", ["proxy", "5000"])
    assert _parse_slash_text("/STATUS@MyBot now") == ("status", ["now"])
    assert _parse_slash_text("not a command") is None
    assert _parse_slash_text("/") is None


def test_sanitize_filename():
    assert _sanitize_filename("../../etc/passwd") == "passwd"
    assert _sanitize_filename("my report (v2).pdf") == "my report _v2_.pdf"
    assert _sanitize_filename("") == "upload.bin"
    assert _sanitize_filename("..\\..\\boot.ini") != "..\\..\\boot.ini"


def test_prepare_prompt_with_images_no_images():
    state = {"pending_images": [], "image_history": []}
    prompt, used = _prepare_prompt_with_images(state, "hello")
    assert prompt == "hello"
    assert used is False


def test_prepare_prompt_with_images_pending():
    img = {"path": "/abs/img.png", "relative_path": "images/img.png", "file_name": "img.png"}
    state = {"pending_images": [img], "image_history": [img]}
    prompt, used = _prepare_prompt_with_images(state, "describe this")
    assert used is True
    assert "images/img.png" in prompt
    assert state["pending_images"] == []


def test_prepare_prompt_with_placeholder_uses_history():
    img = {"path": "/abs/img.png", "relative_path": "images/img.png", "file_name": "img.png"}
    state = {"pending_images": [], "image_history": [img]}
    prompt, used = _prepare_prompt_with_images(state, "look at <image> please")
    assert used is True
    assert "<image>" not in prompt
    assert "images/img.png" in prompt


def test_fmt_bytes():
    assert _fmt_bytes(512) == "512.0 B"
    assert _fmt_bytes(2048) == "2.0 KB"
    assert _fmt_bytes(5 * 1024**3) == "5.0 GB"


def test_get_system_info_runs():
    psutil = pytest.importorskip("psutil")
    del psutil
    info = get_system_info()
    assert "CPU:" in info
    assert "RAM:" in info


def test_list_processes_runs():
    psutil = pytest.importorskip("psutil")
    del psutil
    listing = list_processes(limit=5)
    assert "PID" in listing
    # filter that matches nothing
    assert list_processes("zz_no_such_process_zz") == "No matching processes."
