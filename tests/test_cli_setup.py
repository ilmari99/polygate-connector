"""Tests for the terminal-based ``polygate setup`` command.

These run fully offline: the local server's ``/health`` and ``/setup`` calls are
stubbed so we exercise the CLI's own logic (prompting, validation, live-apply vs
``.env`` fallback) without a real server or network.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from polygate import cli
from polygate.config import get_settings

_FAKE_PK = "0x" + "11" * 32
_FAKE_FUNDER = "0x" + "ab" * 20


@pytest.fixture(autouse=True)
def _data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect any ``.env`` writes to a temp data dir."""
    monkeypatch.setenv("POLYGATE_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()


def _feed_inputs(
    monkeypatch: pytest.MonkeyPatch,
    answers: list[str],
    pk: str = _FAKE_PK,
) -> None:
    """Feed a queue of ``input()`` answers and a fixed ``getpass`` value."""
    it = iter(answers)
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: next(it))
    monkeypatch.setattr(cli.getpass, "getpass", lambda *_a, **_k: pk)


def test_setup_writes_env_when_server_down(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Unconfigured: choose terminal entry [1], then provide the funder address.
    _feed_inputs(monkeypatch, ["1", _FAKE_FUNDER])
    monkeypatch.setattr(cli, "_get_health", lambda _base: None)

    assert cli.run_setup() == 0

    env = (_data_dir / ".env").read_text()
    assert "PRIVATE_KEY=" in env
    assert f"FUNDER_ADDRESS={_FAKE_FUNDER}" in env


def test_setup_applies_live_when_server_running(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed_inputs(monkeypatch, ["1", _FAKE_FUNDER])
    monkeypatch.setattr(cli, "_get_health", lambda _base: {"configured": False})
    captured: dict = {}

    def fake_post(_base: str, payload: bytes) -> tuple[bool, str]:
        captured["payload"] = json.loads(payload.decode())
        return True, ""

    monkeypatch.setattr(cli, "_post_setup", fake_post)

    assert cli.run_setup() == 0
    assert captured["payload"]["funder_address"] == _FAKE_FUNDER
    assert captured["payload"]["private_key"] == _FAKE_PK
    # The live path must not also write .env directly.
    assert not (_data_dir / ".env").exists()


def test_setup_falls_back_to_env_when_live_apply_fails(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed_inputs(monkeypatch, ["1", _FAKE_FUNDER])
    monkeypatch.setattr(cli, "_get_health", lambda _base: {"configured": False})
    monkeypatch.setattr(cli, "_post_setup", lambda _base, _p: (False, "boom"))

    assert cli.run_setup() == 0
    assert (_data_dir / ".env").exists()


def test_setup_browser_option_prints_url_and_exits(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture
) -> None:
    # Unconfigured: choose the browser page [2]; nothing should be written.
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "2")
    monkeypatch.setattr(cli, "_get_health", lambda _base: None)

    assert cli.run_setup() == 0
    assert "/setup" in capsys.readouterr().out
    assert not (_data_dir / ".env").exists()


def test_setup_declines_reconfigure_when_already_configured(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_get_health",
        lambda _base: {"configured": True, "wallet_address": _FAKE_FUNDER},
    )
    # Answer the reconfigure prompt with "no" -> nothing changes.
    monkeypatch.setattr("builtins.input", lambda *_a, **_k: "n")

    assert cli.run_setup() == 0
    assert not (_data_dir / ".env").exists()


def test_setup_reconfigures_when_confirmed(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "_get_health",
        lambda _base: {"configured": True, "wallet_address": _FAKE_FUNDER},
    )
    other_funder = "0x" + "cd" * 20
    # Confirm reconfigure, choose terminal [1], then provide the new funder.
    _feed_inputs(monkeypatch, ["y", "1", other_funder])
    # No live server reachable -> falls back to writing the new wallet to .env.
    monkeypatch.setattr(cli, "_post_setup", lambda _base, _p: (False, "down"))

    assert cli.run_setup() == 0
    env = (_data_dir / ".env").read_text()
    assert f"FUNDER_ADDRESS={other_funder}" in env


def test_setup_rejects_bad_input(
    _data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _feed_inputs(monkeypatch, ["1", "0x1234"], pk="not-a-key")
    monkeypatch.setattr(cli, "_get_health", lambda _base: None)

    assert cli.run_setup() == 1
    assert not (_data_dir / ".env").exists()


def test_dispatch_help(capsys: pytest.CaptureFixture) -> None:
    assert cli.dispatch(["--help"]) == 0
    assert "polygate setup" in capsys.readouterr().out


def test_dispatch_unknown_command(capsys: pytest.CaptureFixture) -> None:
    assert cli.dispatch(["bogus"]) == 2
    assert "Unknown command" in capsys.readouterr().err
