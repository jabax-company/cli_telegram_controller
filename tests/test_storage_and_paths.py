"""Tests for project persistence, audit rotation, and download path resolution."""

from __future__ import annotations

import companion.core.storage as storage
from companion.handlers.system_commands import resolve_download_path


def test_save_and_load_projects_roundtrip(tmp_path, monkeypatch):
    projects_file = tmp_path / "projects.json"
    monkeypatch.setattr(storage, "PROJECTS_FILE", projects_file)
    storage._projects_cache = None
    storage._projects_mtime = None

    assert storage.load_projects() == {}
    storage.save_projects({"demo": "/tmp/demo"})
    assert storage.load_projects() == {"demo": "/tmp/demo"}
    # Cached result is a copy: mutating it must not poison the cache.
    loaded = storage.load_projects()
    loaded["evil"] = "/x"
    assert "evil" not in storage.load_projects()


def test_load_projects_rejects_non_dict(tmp_path, monkeypatch):
    projects_file = tmp_path / "projects.json"
    projects_file.write_text("[1, 2, 3]", encoding="utf-8")
    monkeypatch.setattr(storage, "PROJECTS_FILE", projects_file)
    storage._projects_cache = None
    storage._projects_mtime = None
    assert storage.load_projects() == {}


def test_audit_rotation(tmp_path, monkeypatch):
    log = tmp_path / "audit.log"
    monkeypatch.setattr(storage, "AUDIT_LOG", log)
    monkeypatch.setattr(storage, "AUDIT_MAX_BYTES", 200)

    for i in range(20):
        storage.audit(1, f"entry number {i} with some padding text")

    backup = log.with_suffix(".log.1")
    assert backup.exists(), "rotation should have produced a backup file"
    assert log.stat().st_size < 400


def test_resolve_download_path(tmp_path):
    target = tmp_path / "report.txt"
    target.write_text("data", encoding="utf-8")

    assert resolve_download_path("report.txt", str(tmp_path)) == target.resolve()
    assert resolve_download_path(str(target), "/elsewhere") == target.resolve()
    assert resolve_download_path("missing.txt", str(tmp_path)) is None
    # Directories are not downloadable
    assert resolve_download_path(".", str(tmp_path)) is None
