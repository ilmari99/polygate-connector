"""System endpoints: health, configuration, and first-run setup.

The ``/setup`` page lets a user supply their Polymarket wallet on a freshly
installed server without editing files or running commands: PolyGate can start
unconfigured, serve this local page, and finish onboarding once the keys arrive.
It is deliberately host-agnostic - just two fields plus the automatic steps -
so PolyGate stays a general gateway rather than embedding any one client.

Security: the page is served only while the server is **unconfigured** and only
to **loopback** clients. Secrets are posted straight to this local process and
written to ``.env``; they never leave the machine.
"""

from __future__ import annotations

import html
import ipaddress
import re

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, field_validator

from ..config import get_settings
from ..core.auth import require_api_key
from ..onboarding import complete_onboarding, save_wallet
from ..services.facade import PolymarketService

router = APIRouter(tags=["system"])

_HEX_64 = re.compile(r"^(0x)?[0-9a-fA-F]{64}$")
_ADDRESS = re.compile(r"^0x[0-9a-fA-F]{40}$")


@router.get("/health")
async def health() -> dict:
    """Liveness probe and current run mode (no auth required)."""
    settings = get_settings()
    return {
        "status": "ok",
        "mode": "dry-run" if settings.dry_run else "live",
        "configured": settings.has_wallet,
        "wallet_address": settings.funder_address,
    }


@router.get("/config", dependencies=[Depends(require_api_key)])
async def config() -> dict:
    """Return the active, secret-free configuration summary."""
    return get_settings().public_summary()


@router.get("/", include_in_schema=False)
async def root() -> RedirectResponse:
    """Send browser visits to the useful first-run or API page."""
    target = "/docs" if get_settings().has_wallet else "/setup"
    return RedirectResponse(target, status_code=307)


# --------------------------------------------------------------------------- #
# First-run setup page
# --------------------------------------------------------------------------- #


class SetupRequest(BaseModel):
    """Wallet supplied through the local setup page."""

    private_key: str
    funder_address: str

    @field_validator("private_key")
    @classmethod
    def _valid_private_key(cls, value: str) -> str:
        value = value.strip()
        if not _HEX_64.match(value):
            raise ValueError(
                "PRIVATE_KEY must be a 64-character hex string (with or without a "
                "0x prefix). Reveal it on polymarket.com under "
                "Settings -> Account -> Private Key."
            )
        return value

    @field_validator("funder_address")
    @classmethod
    def _valid_funder_address(cls, value: str) -> str:
        value = value.strip()
        if not _ADDRESS.match(value):
            raise ValueError(
                "FUNDER_ADDRESS must be a 0x-prefixed 42-character address. Find it "
                "on polymarket.com under Settings -> Profile -> Address."
            )
        return value


def _client_is_local(request: Request) -> bool:
    """True only when the request originates from the loopback interface."""
    client = request.client
    if client is None:
        return False
    host = client.host
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return host in {"localhost", "testclient"}


@router.get("/setup", response_class=HTMLResponse, include_in_schema=False)
async def setup_page(request: Request) -> HTMLResponse:
    """Serve the setup form (loopback only). Doubles as the reconfigure form."""
    if not _client_is_local(request):
        return HTMLResponse(_LOCAL_ONLY_HTML, status_code=403)
    settings = get_settings()
    banner = ""
    if settings.has_wallet:
        banner = (
            '<div class="note"><strong>A wallet is already connected</strong> ('
            + html.escape(settings.funder_address or "")
            + "). Submitting below replaces it and re-derives credentials.</div>"
        )
    return HTMLResponse(_SETUP_HTML.replace("<!--BANNER-->", banner), status_code=200)


@router.post("/setup", include_in_schema=False)
async def setup_submit(request: Request, body: SetupRequest) -> JSONResponse:
    """Persist the supplied wallet, finish onboarding, and activate trading.

    Callable from loopback whether or not a wallet is already configured, so it
    serves both first-run setup and later reconfiguration. Writes the keys to
    ``.env`` (clearing any wallet-derived credentials), derives the CLOB
    credentials and signature type (skipped in dry-run), then rebuilds the
    service so account and trading endpoints come live without a restart.
    """
    if not _client_is_local(request):
        return JSONResponse(
            status_code=403,
            content={
                "error": "forbidden",
                "detail": "Setup is only available from the local machine.",
            },
        )
    settings = get_settings()
    reconfigured = settings.has_wallet

    save_wallet(settings, body.private_key, body.funder_address)
    try:
        await complete_onboarding(settings)
    except Exception as exc:  # noqa: BLE001 - surface a clean setup error
        # Roll back the in-memory wallet so the page can be retried; the keys
        # remain in .env for inspection but the server stays unconfigured.
        settings.private_key = None
        settings.funder_address = None
        return JSONResponse(
            status_code=502,
            content={"error": "onboarding_failed", "detail": str(exc)},
        )

    old = getattr(request.app.state, "service", None)
    request.app.state.service = PolymarketService(settings)
    if old is not None:
        await old.aclose()

    return JSONResponse(
        status_code=200,
        content={
            "status": "ok",
            "configured": True,
            "reconfigured": reconfigured,
            "mode": "dry-run" if settings.dry_run else "live",
            "wallet_address": settings.funder_address,
        },
    )


_SETUP_HTML = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>PolyGate setup</title>
  <style>
    :root { color-scheme: light dark; }
    body { font-family: system-ui, sans-serif; max-width: 640px; margin: 2.5rem auto;
           padding: 0 1.25rem; line-height: 1.5; }
    h1 { margin-bottom: 0.25rem; }
    .sub { color: #6b7280; margin-top: 0; }
    label { display: block; font-weight: 600; margin: 1.25rem 0 0.35rem; }
    input { width: 100%; padding: 0.6rem 0.7rem; font-size: 1rem; box-sizing: border-box;
            border: 1px solid #9ca3af; border-radius: 8px; font-family: monospace; }
    .hint { font-size: 0.85rem; color: #6b7280; margin-top: 0.35rem; }
    button { margin-top: 1.5rem; padding: 0.7rem 1.2rem; font-size: 1rem; font-weight: 600;
             border: 0; border-radius: 8px; background: #2563eb; color: #fff; cursor: pointer; }
    button:disabled { opacity: 0.6; cursor: progress; }
    .note { background: #f3f4f6; border-radius: 8px; padding: 0.9rem 1rem; margin: 1.25rem 0;
            font-size: 0.9rem; }
    #result { margin-top: 1.25rem; font-weight: 600; }
    .ok { color: #15803d; }
    .err { color: #b91c1c; }
    a { color: #2563eb; }
  </style>
</head>
<body>
  <h1>PolyGate setup</h1>
  <p class="sub">Connect your Polymarket account. These keys stay on this machine
     &mdash; they are written to a local <code>.env</code> file and never sent anywhere
     but to Polymarket when you trade.</p>
  <!--BANNER-->

  <div class="note">
    <strong>Where to find these</strong> (see the
    <a href="https://github.com/ilmari99/polygate#setup" target="_blank" rel="noopener">README</a>):
    <ul>
      <li><strong>Private key</strong> &mdash; on
          <a href="https://polymarket.com" target="_blank" rel="noopener">polymarket.com</a>:
          <em>Settings &rarr; Account &rarr; Private Key</em>. If you connected your own
          wallet (e.g. MetaMask), export that wallet's key instead.</li>
      <li><strong>Funder address</strong> &mdash; on polymarket.com:
          <em>Settings &rarr; Profile &rarr; Address</em>. This holds your funds.</li>
    </ul>
    Everything else (API credentials, signature type, platform key) is configured
    automatically.
  </div>

  <form id="f">
    <label for="pk">Private key</label>
    <input id="pk" type="password" autocomplete="off" spellcheck="false"
           placeholder="0x..." />
    <div class="hint">Signs your orders. Keep it secret.</div>

    <label for="fa">Funder address</label>
    <input id="fa" type="text" autocomplete="off" spellcheck="false"
           placeholder="0x..." />
    <div class="hint">Your Polymarket address that holds your USDC.</div>

    <button id="b" type="submit">Connect account</button>
  </form>

  <div id="result"></div>

  <script>
    const f = document.getElementById('f');
    const b = document.getElementById('b');
    const out = document.getElementById('result');
    f.addEventListener('submit', async (e) => {
      e.preventDefault();
      out.textContent = '';
      out.className = '';
      b.disabled = true;
      b.textContent = 'Connecting...';
      try {
        const resp = await fetch('/setup', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            private_key: document.getElementById('pk').value,
            funder_address: document.getElementById('fa').value,
          }),
        });
        const data = await resp.json();
        if (resp.ok) {
          out.className = 'ok';
          out.textContent = 'Connected wallet ' + (data.wallet_address || '')
            + ' (' + data.mode + '). You can close this page and use the agent.';
          f.style.display = 'none';
        } else {
          out.className = 'err';
          out.textContent = (data.detail || 'Setup failed.');
          b.disabled = false;
          b.textContent = 'Connect account';
        }
      } catch (err) {
        out.className = 'err';
        out.textContent = 'Could not reach the server: ' + err;
        b.disabled = false;
        b.textContent = 'Connect account';
      }
    });
  </script>
</body>
</html>"""


_LOCAL_ONLY_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8" /><title>PolyGate</title>
<style>body{font-family:system-ui,sans-serif;max-width:640px;margin:2.5rem auto;
padding:0 1.25rem;line-height:1.5}</style></head>
<body><h1>Setup is local-only</h1>
<p>The PolyGate setup page can only be opened from the machine running the
server.</p></body></html>"""
