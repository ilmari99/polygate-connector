from __future__ import annotations

import base64
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from polygate import cli
from polygate.config import get_settings
from polygate.connect import (
    ConnectCredentials,
    OAuthState,
    create_connect_app,
    load_or_create_credentials,
)


def _challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def test_connect_credentials_persist_and_rotate(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("POLYGATE_DATA_DIR", str(tmp_path))

    first = load_or_create_credentials()
    reused = load_or_create_credentials()
    rotated = load_or_create_credentials(rotate=True)

    assert reused == first
    assert rotated != first
    assert (tmp_path / "connect-credentials.json").exists()


def test_oauth_flow_gates_mcp_and_issues_token() -> None:
    creds = ConnectCredentials("polygate-test", "pg_secret_test")
    state = OAuthState(credentials=creds, public_url="https://example.trycloudflare.com")
    client_cm = TestClient(create_connect_app(state))

    with client_cm as client:
        unauth = client.get("/mcp")
        assert unauth.status_code == 401
        assert "WWW-Authenticate" in unauth.headers

        metadata = client.get("/.well-known/oauth-authorization-server").json()
        assert metadata["token_endpoint"] == "https://example.trycloudflare.com/token"

        verifier = "test-verifier"
        auth = client.post(
            "/authorize",
            data={
                "client_id": creds.client_id,
                "redirect_uri": "https://ai.example/callback",
                "state": "abc",
                "code_challenge": _challenge(verifier),
                "code_challenge_method": "S256",
            },
            follow_redirects=False,
        )
        assert auth.status_code == 303
        params = parse_qs(urlparse(auth.headers["location"]).query)
        assert params["state"] == ["abc"]

        token = client.post(
            "/token",
            data={
                "grant_type": "authorization_code",
                "client_id": creds.client_id,
                "client_secret": creds.client_credential,
                "code": params["code"][0],
                "redirect_uri": "https://ai.example/callback",
                "code_verifier": verifier,
            },
        )
        assert token.status_code == 200
        access_token = token.json()["access_token"]

        authed = client.get(
            "/mcp", headers={"Authorization": f"Bearer {access_token}"}
        )
        assert authed.status_code != 401


def test_connect_dispatch_prints_connection_card(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("POLYGATE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("PRIVATE_KEY", raising=False)
    monkeypatch.delenv("FUNDER_ADDRESS", raising=False)
    get_settings.cache_clear()

    class _Server:
        should_exit = False

    class _Tunnel:
        def poll(self):
            """Mimic subprocess.Popen.poll(): return exit code or None."""
            return 0

    monkeypatch.setattr("polygate.connect._start_uvicorn", lambda *a, **k: (_Server(), None))
    monkeypatch.setattr(
        "polygate.connect._start_tunnel",
        lambda _port: (_Tunnel(), "https://unit-test.trycloudflare.com"),
    )
    monkeypatch.setattr("polygate.connect._wait_until_interrupted", lambda: None)
    monkeypatch.setattr("polygate.connect._stop_tunnel", lambda _proc: None)

    assert cli.dispatch(["connect"]) == 0
    out = capsys.readouterr().out
    assert "https://unit-test.trycloudflare.com/mcp" in out
    assert "Client ID" in out
    assert "Client secret" in out
    assert "RESEARCH ONLY" in out
    assert "polygate setup" in out
