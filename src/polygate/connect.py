"""Expose PolyGate as a remote, OAuth-protected MCP server."""

from __future__ import annotations

import argparse
import base64
import binascii
import hashlib
import html
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from mcp.server.transport_security import TransportSecuritySettings
from starlette.datastructures import Headers
from starlette.responses import Response

from .config import get_settings
from .core.env_file import data_dir

_CREDENTIALS_FILE = "connect-credentials.json"
_OAUTH_SCOPE = "mcp"
_TUNNEL_STARTUP_TIMEOUT = 45


@dataclass
class ConnectCredentials:
    client_id: str
    client_credential: str


@dataclass
class OAuthState:
    credentials: ConnectCredentials
    public_url: str = ""
    codes: dict[str, dict[str, str]] = field(default_factory=dict)
    tokens: set[str] = field(default_factory=set)
    clients: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.clients[self.credentials.client_id] = self.credentials.client_credential

    @property
    def issuer(self) -> str:
        return self.public_url.rstrip("/")


def credentials_path() -> Path:
    return data_dir() / _CREDENTIALS_FILE


def _new_credentials() -> ConnectCredentials:
    return ConnectCredentials(
        client_id=f"polygate-{secrets.token_urlsafe(12)}",
        client_credential=f"pg_secret_{secrets.token_urlsafe(32)}",
    )


def load_or_create_credentials(*, rotate: bool = False) -> ConnectCredentials:
    """Load persisted OAuth client credentials, or create them on first use."""
    path = credentials_path()
    if not rotate and path.exists():
        raw = json.loads(path.read_text(encoding="utf-8"))
        return ConnectCredentials(
            client_id=raw["client_id"],
            client_credential=raw["client_secret"],
        )

    creds = _new_credentials()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"client_id": creds.client_id, "client_secret": creds.client_credential},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(path, 0o600)
    return creds


def _metadata(state: OAuthState) -> dict[str, Any]:
    issuer = state.issuer
    return {
        "issuer": issuer,
        "authorization_endpoint": f"{issuer}/authorize",
        "token_endpoint": f"{issuer}/token",
        "registration_endpoint": f"{issuer}/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
        "code_challenge_methods_supported": ["S256", "plain"],
        "scopes_supported": [_OAUTH_SCOPE],
    }


def _resource_metadata(state: OAuthState) -> dict[str, Any]:
    issuer = state.issuer
    return {
        "resource": f"{issuer}/mcp",
        "resource_name": "PolyGate MCP",
        "authorization_servers": [issuer],
        "bearer_methods_supported": ["header"],
        "scopes_supported": [_OAUTH_SCOPE],
    }


def _pkce_matches(verifier: str, challenge: str, method: str) -> bool:
    if method == "plain":
        expected = verifier
    else:
        digest = hashlib.sha256(verifier.encode("ascii")).digest()
        expected = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
    return secrets.compare_digest(expected, challenge)


def _client_secret_from_basic(request: Request) -> tuple[str | None, str | None]:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("basic "):
        return None, None
    try:
        decoded = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
        client_id, client_secret = decoded.split(":", 1)
        return client_id, client_secret
    except (ValueError, UnicodeDecodeError, binascii.Error):
        return None, None


class BearerAuthASGI:
    """Small ASGI wrapper that requires OAuth bearer tokens for MCP traffic."""

    def __init__(self, app: Any, state: OAuthState) -> None:
        self.app = app
        self.state = state

    async def __call__(self, scope: dict, receive: Any, send: Any) -> None:
        if scope["type"] == "http" and scope["path"].startswith("/mcp"):
            headers = Headers(scope=scope)
            auth = headers.get("authorization", "")
            token = auth.removeprefix("Bearer ").strip()
            if not auth.startswith("Bearer ") or token not in self.state.tokens:
                response = JSONResponse(
                    {"error": "unauthorized", "detail": "OAuth bearer token required."},
                    status_code=401,
                    headers={
                        "WWW-Authenticate": (
                            f'Bearer resource_metadata="{self.state.issuer}'
                            '/.well-known/oauth-protected-resource"'
                        )
                    },
                )
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


def create_connect_app(state: OAuthState) -> FastAPI:
    """Create the OAuth authorization server plus mounted MCP HTTP app."""
    from . import mcp_server

    mcp_server.mcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False
    )
    mcp_app = mcp_server.mcp.streamable_http_app()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        async with mcp_server.mcp.session_manager.run():
            yield

    app = FastAPI(title="PolyGate Connect", lifespan=lifespan)

    @app.get("/.well-known/oauth-authorization-server")
    async def oauth_authorization_server() -> dict[str, Any]:
        return _metadata(state)

    @app.get("/.well-known/openid-configuration")
    async def openid_configuration() -> dict[str, Any]:
        return _metadata(state)

    @app.get("/.well-known/oauth-protected-resource")
    async def oauth_protected_resource() -> dict[str, Any]:
        return _resource_metadata(state)

    @app.post("/register")
    async def register() -> dict[str, Any]:
        client = _new_credentials()
        state.clients[client.client_id] = client.client_credential
        return {
            "client_id": client.client_id,
            "client_secret": client.client_credential,
            "client_id_issued_at": int(time.time()),
            "token_endpoint_auth_method": "client_secret_post",
            "grant_types": ["authorization_code"],
            "response_types": ["code"],
            "scope": _OAUTH_SCOPE,
        }

    @app.get("/authorize")
    async def authorize_form(
        client_id: str,
        redirect_uri: str,
        state: str | None = None,
        code_challenge: str | None = None,
        code_challenge_method: str = "S256",
        scope: str = _OAUTH_SCOPE,
    ) -> HTMLResponse:
        if client_id not in app.state.oauth.clients:
            raise HTTPException(status_code=400, detail="Unknown OAuth client_id.")
        if code_challenge_method not in ("S256", "plain"):
            raise HTTPException(status_code=400, detail="Unsupported PKCE method.")
        fields = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state or "",
            "code_challenge": code_challenge or "",
            "code_challenge_method": code_challenge_method,
            "scope": scope,
        }
        inputs = "\n".join(
            f'<input type="hidden" name="{html.escape(name, quote=True)}" '
            f'value="{html.escape(value, quote=True)}">'
            for name, value in fields.items()
        )
        return HTMLResponse(
            f"""<!doctype html>
<title>Authorize PolyGate</title>
<h1>Authorize PolyGate</h1>
<p>Allow this web AI connector to access your local PolyGate MCP server.</p>
<form method="post" action="/authorize">
{inputs}
<button type="submit">Authorize</button>
</form>"""
        )

    @app.post("/authorize")
    async def authorize_submit(request: Request) -> Response:
        form = await request.form()
        client_id = str(form.get("client_id", ""))
        if client_id not in app.state.oauth.clients:
            raise HTTPException(status_code=400, detail="Unknown OAuth client_id.")
        code = secrets.token_urlsafe(32)
        app.state.oauth.codes[code] = {
            "client_id": client_id,
            "redirect_uri": str(form.get("redirect_uri", "")),
            "code_challenge": str(form.get("code_challenge", "")),
            "code_challenge_method": str(form.get("code_challenge_method", "S256")),
        }
        params = {"code": code}
        returned_state = str(form.get("state", ""))
        if returned_state:
            params["state"] = returned_state
        separator = "&" if "?" in app.state.oauth.codes[code]["redirect_uri"] else "?"
        return RedirectResponse(
            app.state.oauth.codes[code]["redirect_uri"] + separator + urlencode(params),
            status_code=303,
        )

    @app.post("/token")
    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        grant_type = str(form.get("grant_type", ""))
        if grant_type != "authorization_code":
            raise HTTPException(status_code=400, detail="Unsupported grant_type.")

        basic_id, basic_secret = _client_secret_from_basic(request)
        client_id = basic_id or str(form.get("client_id", ""))
        client_secret = basic_secret or str(form.get("client_secret", ""))
        if app.state.oauth.clients.get(client_id) != client_secret:
            raise HTTPException(status_code=401, detail="Invalid OAuth client credentials.")

        code = str(form.get("code", ""))
        record = app.state.oauth.codes.pop(code, None)
        if record is None or record["client_id"] != client_id:
            raise HTTPException(status_code=400, detail="Invalid authorization code.")
        redirect_uri = str(form.get("redirect_uri", ""))
        if redirect_uri and redirect_uri != record["redirect_uri"]:
            raise HTTPException(status_code=400, detail="redirect_uri does not match.")
        challenge = record.get("code_challenge")
        verifier = str(form.get("code_verifier", ""))
        if challenge and not _pkce_matches(verifier, challenge, record["code_challenge_method"]):
            raise HTTPException(status_code=400, detail="Invalid PKCE verifier.")

        access_token = secrets.token_urlsafe(32)
        app.state.oauth.tokens.add(access_token)
        return JSONResponse(
            {
                "access_token": access_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": _OAUTH_SCOPE,
            }
        )

    app.state.oauth = state

    app.mount("/", BearerAuthASGI(mcp_app, state))
    return app


def _start_uvicorn(app: FastAPI, *, port: int) -> tuple[Any, threading.Thread]:
    import uvicorn

    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            return server, thread
        if not thread.is_alive():
            break
        time.sleep(0.1)
    raise RuntimeError("PolyGate HTTP server did not start.")


def _find_cloudflared() -> str:
    exe = shutil.which("cloudflared")
    if exe is None:
        raise RuntimeError(_cloudflared_install_help())
    return exe


def _cloudflared_install_help() -> str:
    return """cloudflared is required for `polygate connect`.

Install it, then re-run `polygate connect`:
  macOS:   brew install cloudflared
  Windows: winget install --id Cloudflare.cloudflared
  Linux:   download the package from https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/
"""


def _start_tunnel(port: int) -> tuple[subprocess.Popen[str], str]:
    exe = _find_cloudflared()
    proc = subprocess.Popen(
        [exe, "tunnel", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    assert proc.stdout is not None
    # Quick tunnels can spend several seconds selecting an edge and printing the URL.
    deadline = time.time() + _TUNNEL_STARTUP_TIMEOUT
    url_re = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")
    lines: list[str] = []
    while time.time() < deadline:
        line = proc.stdout.readline()
        if line:
            lines.append(line.strip())
            match = url_re.search(line)
            if match:
                return proc, match.group(0)
        elif proc.poll() is not None:
            break
    proc.terminate()
    raise RuntimeError("Could not open a Cloudflare quick tunnel.\n" + "\n".join(lines[-10:]))


def _stop_tunnel(proc: subprocess.Popen[str]) -> None:
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


def _wait_until_interrupted() -> None:
    while True:
        time.sleep(3600)


def _print_card(public_url: str, creds: ConnectCredentials, *, trading: bool, has_wallet: bool) -> None:
    mode = "LIVE TRADING ENABLED" if trading else "RESEARCH ONLY"
    setup_hint = "" if has_wallet else "  (no wallet set; run `polygate setup` to enable trading later)"
    print()
    print("PolyGate is connected and reachable by a web AI.")
    print()
    print(f"  MCP URL / Server URL:       {public_url.rstrip('/')}/mcp")
    print(f"  Client ID / OAuth ID:       {creds.client_id}")
    # Required copy-paste OAuth credential for the connection card.
    print(f"  Client secret / OAuth secret: {creds.client_credential}   (treat this like a password)")
    print(f"  Mode:                       {mode}{setup_hint}")
    print()
    print("Paste these three values into your AI connector screen.")
    print("! Treat this terminal output as sensitive. Anyone with this URL and credentials can use your PolyGate.")
    print("  Ctrl-C to disconnect.")


def run_connect(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="polygate connect",
        description="Expose PolyGate to web AIs through an OAuth-protected HTTPS MCP endpoint.",
    )
    parser.add_argument("--new-credentials", action="store_true", help="Rotate the OAuth client ID and secret.")
    parser.add_argument("--allow-trading", action="store_true", help="Enable live trading after typed confirmation.")
    parser.add_argument("--port", type=int, default=8765, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    get_settings.cache_clear()
    settings = get_settings()
    trading = False
    if args.allow_trading:
        if not settings.has_wallet:
            print("No wallet is set, so PolyGate will run in research-only mode.")
            print("Run `polygate setup` first, then re-run with --allow-trading.")
        else:
            phrase = f"ALLOW TRADING {secrets.token_hex(3).upper()}"
            answer = input(f"Type {phrase!r} to enable live trading: ").strip()
            if answer != phrase:
                print("Trading was not enabled; starting in research-only mode.")
            else:
                trading = True

    if not trading:
        os.environ["DRY_RUN"] = "true"
        get_settings.cache_clear()

    creds = load_or_create_credentials(rotate=args.new_credentials)
    state = OAuthState(credentials=creds)
    server = None
    thread: threading.Thread | None = None
    tunnel: subprocess.Popen[str] | None = None
    try:
        app = create_connect_app(state)
        server, thread = _start_uvicorn(app, port=args.port)
        tunnel, public_url = _start_tunnel(args.port)
        state.public_url = public_url
        _print_card(public_url, creds, trading=trading, has_wallet=settings.has_wallet)
        _wait_until_interrupted()
    except KeyboardInterrupt:
        print("\nDisconnected PolyGate.")
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    finally:
        if tunnel is not None:
            _stop_tunnel(tunnel)
        if server is not None:
            server.should_exit = True
        if thread is not None:
            thread.join(timeout=5)
    return 0
