"""Command-line entry points beyond running the server.

``polygate setup`` connects a Polymarket wallet from a terminal. This is the
friction-free path when PolyGate runs on a remote server, where the loopback-only
``/setup`` web page can't be opened from your browser: you run this on the same
machine (over plain SSH, no port forwarding), the private key is read with a
hidden prompt, and it is applied to the locally running server (or written to
``.env``). The key never crosses an open network port and never reaches the
trading agent.
"""

from __future__ import annotations

import getpass
import json
import sys
import urllib.error
import urllib.request

from pydantic import ValidationError

from .api.routes_system import SetupRequest
from .config import get_settings
from .onboarding import save_wallet

_USAGE = """\
PolyGate - REST gateway to Polymarket.

Usage:
  polygate           Start the gateway server.
  polygate setup     Connect your Polymarket wallet (interactive, terminal-based).
  polygate --help    Show this message.
"""


def _get_health(base: str) -> dict | None:
    """Return the local server's ``/health`` JSON, or ``None`` if it isn't up."""
    try:
        with urllib.request.urlopen(f"{base}/health", timeout=2) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        return None


def _post_setup(base: str, payload: bytes) -> tuple[bool, str]:
    """POST the wallet to the local ``/setup`` endpoint. Returns ``(ok, detail)``."""
    req = urllib.request.Request(
        f"{base}/setup",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120):
            return True, ""
    except urllib.error.HTTPError as exc:
        try:
            body = json.loads(exc.read().decode())
            return False, body.get("detail") or body.get("error") or str(exc)
        except Exception:
            return False, str(exc)
    except Exception as exc:
        return False, str(exc)


def run_setup() -> int:
    """Interactive wallet setup: pick terminal or browser, supports reconfigure."""
    settings = get_settings()
    base = f"http://127.0.0.1:{settings.port}"
    health = _get_health(base)

    print("PolyGate setup - connect your Polymarket wallet.")
    if health is not None and health.get("configured"):
        addr = health.get("wallet_address")
        print(f"A wallet is already connected ({addr}).")
        ans = input("Reconfigure with a different wallet? [y/N]: ").strip().lower()
        if ans not in ("y", "yes"):
            print("Nothing changed.")
            return 0

    print()
    print("How do you want to enter your wallet?")
    print("  [1] Here in the terminal  (works on a remote server, e.g. over SSH)")
    print(f"  [2] In a browser at {base}/setup  (easiest on your own computer)")
    choice = (input("Choose [1/2] (default 1): ").strip() or "1")
    if choice == "2":
        print()
        print(f"Open {base}/setup in a browser on this machine and enter your wallet there.")
        print("If your browser is on another computer, re-run setup and choose [1].")
        return 0
    if choice != "1":
        print(f"Unrecognized choice: {choice}", file=sys.stderr)
        return 2

    print()
    print("Keys are stored in a local .env and only ever sent to Polymarket when you trade.")
    print("Find these on polymarket.com:")
    print("  - Funder address: Settings -> Profile -> Address")
    print("  - Private key:    Settings -> Account -> Private Key")
    print()
    funder = input("Funder address (0x...): ").strip()
    private_key = getpass.getpass("Private key (hidden input, 0x...): ").strip()

    try:
        SetupRequest(private_key=private_key, funder_address=funder)
    except ValidationError as exc:
        print("\nInvalid input:", file=sys.stderr)
        for err in exc.errors():
            print(f"  - {err.get('msg')}", file=sys.stderr)
        return 1

    if health is not None:
        # A local server is up - apply live so trading works without a restart.
        payload = json.dumps(
            {"private_key": private_key, "funder_address": funder}
        ).encode()
        ok, detail = _post_setup(base, payload)
        if ok:
            print(f"\nConnected wallet {funder}. Trading is live now - no restart needed.")
            return 0
        print(f"\nCould not apply via the running server: {detail}")
        print("Writing the wallet to .env instead ...")

    path = save_wallet(settings, private_key, funder)
    print(f"\nWallet saved to {path}.")
    print("Start PolyGate (or restart the OpenClaw gateway) to finish setup and go live.")
    return 0


def dispatch(args: list[str]) -> int:
    """Route a non-empty argv tail to a subcommand. Returns an exit code."""
    cmd = args[0]
    if cmd == "setup":
        return run_setup()
    if cmd in ("-h", "--help", "help"):
        print(_USAGE)
        return 0
    print(f"Unknown command: {cmd}\n", file=sys.stderr)
    print(_USAGE, file=sys.stderr)
    return 2
