"""Path resolution helpers."""

from __future__ import annotations

import os
from pathlib import Path

from companion.core.config import BASE_DIR
from companion.core.storage import load_projects


def resolve_path(
    arg: str,
    current_dir: str | None = None,
    base_dir: str | None = None,
) -> str | None:
    projects = load_projects()
    if arg in projects and os.path.isdir(projects[arg]):
        return str(Path(projects[arg]).resolve())

    expanded = os.path.expanduser(arg)

    if os.name == "nt" and expanded.startswith(("/", "\\")):
        rel = expanded.lstrip("/\\")
        pref_bases: list[Path] = []
        if base_dir:
            pref_bases.append(Path(base_dir))
        if current_dir:
            pref_bases.append(Path(current_dir))
        pref_bases.append(BASE_DIR)
        for base in pref_bases:
            candidate = base / rel
            if candidate.is_dir():
                return str(candidate.resolve())

    if os.path.isabs(expanded):
        return str(Path(expanded).resolve()) if os.path.isdir(expanded) else None

    bases: list[Path] = []
    if current_dir:
        bases.append(Path(current_dir))
    if base_dir:
        bases.append(Path(base_dir))
    else:
        bases.append(BASE_DIR)
    bases.append(Path.home())

    for base in bases:
        candidate = base / arg
        if candidate.is_dir():
            return str(candidate.resolve())
    return None

