"""Minimal example agent for PolyGate.

This is a *template*, not a strategy. It shows the request/response shape an agent
uses to drive the platform on its own cadence:

1. pick a market from Gamma,
2. read the order book / midpoint for one of its outcome tokens,
3. decide something trivial, and
4. submit an order.

Run it (platform must be running on http://127.0.0.1:8000):

    PLATFORM_API_KEY=... ./.venv/bin/python examples/sample_agent.py

It uses only the standard library so it can serve as a reference for any language.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

BASE_URL = os.environ.get("PLATFORM_URL", "http://127.0.0.1:8000")
API_KEY = os.environ.get("PLATFORM_API_KEY")
POLL_SECONDS = float(os.environ.get("POLL_SECONDS", "60"))


def _request(method: str, path: str, body: dict | None = None) -> dict:
    """Make one authenticated JSON request to the platform."""
    if not API_KEY:
        raise SystemExit("Set PLATFORM_API_KEY (see your .env) before running.")
    url = f"{BASE_URL}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("X-API-Key", API_KEY)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:  # surface the platform's error body
        detail = exc.read().decode()
        raise SystemExit(f"{method} {path} -> HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Cannot reach the platform at {url}: {exc.reason}") from exc


def pick_token() -> tuple[str, str]:
    """Return (question, token_id) for the first active market we can read."""
    env = _request("GET", "/markets?limit=20&active=true&closed=false")
    for market in env["data"]:
        question = market.get("question", "<unknown>")
        # Gamma encodes the outcome token ids as a JSON string array.
        raw = market.get("clobTokenIds")
        if not raw:
            continue
        token_ids = json.loads(raw) if isinstance(raw, str) else raw
        if token_ids:
            return question, str(token_ids[0])
    raise SystemExit("No tradable market with token ids found.")


def step(question: str, token_id: str) -> None:
    """One observe-decide-act cycle."""
    midpoint = _request("GET", f"/midpoint/{token_id}")
    price = float(midpoint["data"].get("mid", 0) or 0)
    print(f"[{time.strftime('%X')}] '{question[:48]}' token={token_id} mid={price:.3f}")

    # --- Trivial placeholder decision: bid one tick below the midpoint. ---
    if price <= 0 or price >= 1:
        print("  midpoint unusable; skipping this cycle.")
        return
    bid = round(max(0.01, price - 0.01), 2)
    result = _request(
        "POST",
        "/orders",
        {"token_id": token_id, "side": "BUY", "size": 5, "price": bid, "order_type": "GTC"},
    )
    tag = "SIMULATED" if result.get("simulated") else result.get("status")
    print(f"  order BUY 5 @ {bid} -> {tag} (id={result.get('order_id')})")


def main() -> None:
    health = _request("GET", "/health")
    print(f"Connected to platform in {health.get('mode')} mode.")
    question, token_id = pick_token()
    print(f"Watching: {question}\n")
    while True:
        try:
            step(question, token_id)
        except SystemExit:
            raise
        except Exception as exc:  # keep the loop alive on transient errors
            print(f"  cycle error: {exc}")
        time.sleep(POLL_SECONDS)


if __name__ == "__main__":
    main()
