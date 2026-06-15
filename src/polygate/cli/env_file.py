"""Helpers for safely reading/writing the gitignored ``.env`` file."""

from __future__ import annotations

import os
from pathlib import Path


def find_env_path() -> Path:
    """Locate the project's ``.env`` (cwd first, else repo root next to src)."""
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    # repo root = three parents up from this file: cli/ -> polygate/ -> src/ -> root
    return Path(__file__).resolve().parents[3] / ".env"


def upsert_env(path: Path, values: dict[str, str]) -> None:
    """Insert or update ``KEY=value`` pairs in ``path`` without touching others.

    Preserves comments and ordering; appends new keys at the end. Re-applies
    ``chmod 600`` afterwards so secrets stay private.
    """
    lines: list[str] = []
    if path.exists():
        lines = path.read_text().splitlines()

    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    for key, value in remaining.items():
        out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n")
    os.chmod(path, 0o600)
