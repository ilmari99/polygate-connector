"""Tests for the first-run /setup page and the unconfigured-start behaviour.

These run fully offline. In dry-run mode (the test default) onboarding performs
no signing or network calls, so the setup flow can be exercised end to end. The
``.env`` write is redirected to a temp file so no real configuration is touched.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import polygate.onboarding as onboarding
from polygate.config import get_settings
from polygate.main import create_app

# A throwaway, well-formed (but unfunded/fake) wallet purely for format checks.
_FAKE_PK = "0x" + "11" * 32
_FAKE_FUNDER = "0x" + "ab" * 20


@pytest.fixture
def local_client() -> TestClient:
    """A TestClient whose requests appear to come from loopback."""
    return TestClient(create_app(), client=("127.0.0.1", 12345))


@pytest.fixture(autouse=True)
def _redirect_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Send any .env writes from onboarding to a temp file."""
    env = tmp_path / ".env"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(onboarding, "find_env_path", lambda: env)
    return env


def test_health_reports_unconfigured_by_default():
    with TestClient(create_app()) as client:
        body = client.get("/health").json()
        assert body["configured"] is False


def test_setup_page_served_locally_when_unconfigured(local_client: TestClient):
    with local_client as client:
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "PolyGate setup" in resp.text
        # Key-source instructions must be present.
        assert "Settings &rarr; Account &rarr; Private Key" in resp.text
        assert "Settings &rarr; Profile &rarr; Address" in resp.text


def test_root_redirects_to_setup_when_unconfigured(local_client: TestClient):
    with local_client as client:
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/setup"


def test_setup_page_rejects_remote_client():
    remote = TestClient(create_app(), client=("8.8.8.8", 5555))
    with remote as client:
        resp = client.get("/setup")
        assert resp.status_code == 403
        assert "local-only" in resp.text.lower()


def test_setup_submit_rejects_bad_private_key(local_client: TestClient):
    with local_client as client:
        resp = client.post(
            "/setup",
            json={"private_key": "not-a-key", "funder_address": _FAKE_FUNDER},
        )
        assert resp.status_code == 422


def test_setup_submit_rejects_bad_funder_address(local_client: TestClient):
    with local_client as client:
        resp = client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": "0x1234"},
        )
        assert resp.status_code == 422


def test_setup_submit_rejects_remote_client():
    remote = TestClient(create_app(), client=("203.0.113.7", 9))
    with remote as client:
        resp = client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        assert resp.status_code == 403


def test_setup_submit_configures_wallet(local_client: TestClient, _redirect_env: Path):
    with local_client as client:
        assert client.get("/health").json()["configured"] is False

        resp = client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["configured"] is True
        assert body["wallet_address"] == _FAKE_FUNDER

        # Server now reports configured and the keys were persisted to the env file.
        assert client.get("/health").json()["configured"] is True
        written = _redirect_env.read_text()
        assert "PRIVATE_KEY=" in written
        assert f"FUNDER_ADDRESS={_FAKE_FUNDER}" in written


def test_setup_submit_conflicts_when_already_configured(local_client: TestClient):
    with local_client as client:
        first = client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        assert first.status_code == 200
        second = client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        assert second.status_code == 409
        assert second.json()["error"] == "already_configured"


def test_setup_page_shows_configured_after_setup(local_client: TestClient):
    with local_client as client:
        client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        resp = client.get("/setup")
        assert resp.status_code == 200
        assert "already connected" in resp.text.lower()


def test_root_redirects_to_docs_when_configured(local_client: TestClient):
    with local_client as client:
        client.post(
            "/setup",
            json={"private_key": _FAKE_PK, "funder_address": _FAKE_FUNDER},
        )
        resp = client.get("/", follow_redirects=False)
        assert resp.status_code == 307
        assert resp.headers["location"] == "/docs"
