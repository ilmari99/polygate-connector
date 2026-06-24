"""Tests for ``.env`` path resolution.

The key must land in a stable data dir (``POLYGATE_DATA_DIR`` / ``~/.polygate``),
never in the ephemeral package install directory used by ``uvx`` — otherwise the
OpenClaw plugin and the server disagree on where ``PLATFORM_API_KEY`` lives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from polygate.core.env_file import data_dir, find_env_path, upsert_env


def test_data_dir_honors_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("POLYGATE_DATA_DIR", str(tmp_path))
    assert data_dir() == tmp_path
    assert find_env_path() == tmp_path / ".env"


def test_data_dir_defaults_to_home(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("POLYGATE_DATA_DIR", raising=False)
    assert data_dir() == Path.home() / ".polygate"


def test_find_env_path_never_uses_package_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
):
    # No override, and a cwd with no .env: must fall back to the data dir, not to
    # a path inside the installed package.
    monkeypatch.delenv("POLYGATE_DATA_DIR", raising=False)
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.chdir(empty)
    resolved = find_env_path()
    assert "site-packages" not in str(resolved)
    assert "archive-v0" not in str(resolved)


def test_upsert_env_creates_missing_data_dir(tmp_path: Path):
    target = tmp_path / "nested" / "dir" / ".env"
    upsert_env(target, {"PLATFORM_API_KEY": "abc123"})
    assert target.exists()
    assert "PLATFORM_API_KEY=abc123" in target.read_text()
    assert (target.stat().st_mode & 0o777) == 0o600


def test_config_reads_from_the_file_setup_writes(monkeypatch: pytest.MonkeyPatch):
    """The startup config and the setup writer must resolve the same ``.env``.

    Regression for a path mismatch where the wallet was written to the data dir
    but read back from a cwd-relative ``.env``, so it silently vanished on
    restart.
    """
    from polygate.config import _env_file

    assert _env_file() == str(find_env_path())
