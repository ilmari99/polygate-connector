"""Helpers for safely reading/writing the gitignored ``.env`` file."""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_DATA_DIR = Path.home() / ".polygate"


def data_dir() -> Path:
    """The directory PolyGate persists its ``.env`` (key + wallet) into.

    Honors ``POLYGATE_DATA_DIR`` (set by the OpenClaw plugin on spawn) so the
    server and the plugin always agree on where the key lives; defaults to
    ``~/.polygate``. Never tied to the package install location, which is
    ephemeral under ``uvx``.
    """
    override = os.environ.get("POLYGATE_DATA_DIR")
    if override:
        return Path(override).expanduser()
    return DEFAULT_DATA_DIR


def find_env_path() -> Path:
    """Locate the ``.env`` to read/write.

    Precedence: explicit ``POLYGATE_DATA_DIR`` wins, then an existing ``.env`` in
    the cwd or repo root (local development), else the default data dir. The old
    behaviour of writing next to ``__file__`` is gone — under ``uvx`` that path
    is an ephemeral cache dir that changes every version.
    """
    override = os.environ.get("POLYGATE_DATA_DIR")
    if override:
        return Path(override).expanduser() / ".env"
    cwd_env = Path.cwd() / ".env"
    if cwd_env.exists():
        return cwd_env
    # repo root = three parents up from this file: core/ -> polygate/ -> src/ -> root
    repo_env = Path(__file__).resolve().parents[3] / ".env"
    if repo_env.exists():
        return repo_env
    return DEFAULT_DATA_DIR / ".env"


def upsert_env(
    path: Path,
    values: dict[str, str],
    remove: tuple[str, ...] = (),
) -> None:
    """Insert or update ``KEY=value`` pairs in ``path`` without touching others.

    Preserves comments and ordering; appends new keys at the end. Keys listed in
    ``remove`` are deleted (used to drop wallet-derived credentials when the
    wallet changes). Creates the parent directory if needed and re-applies
    ``chmod 600`` afterwards so secrets stay private.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.exists():
        lines = path.read_text().splitlines()

    drop = set(remove)
    remaining = dict(values)
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            key = stripped.split("=", 1)[0].strip()
            if key in drop:
                continue
            if key in remaining:
                out.append(f"{key}={remaining.pop(key)}")
                continue
        out.append(line)

    for key, value in remaining.items():
        out.append(f"{key}={value}")

    path.write_text("\n".join(out) + "\n")
    os.chmod(path, 0o600)
