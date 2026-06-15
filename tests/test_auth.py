"""Tests for platform API-key authentication."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from polygate.main import create_app


def test_health_is_public():
    with TestClient(create_app()) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "dry-run"


@respx.mock
def test_market_data_is_public():
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(200, json=[{"conditionId": "0xabc"}])
    )
    with TestClient(create_app()) as client:
        # No X-API-Key header: market data is public.
        resp = client.get("/markets")
        assert resp.status_code == 200
        assert resp.json()["source"] == "gamma"


def test_portfolio_requires_key():
    with TestClient(create_app()) as client:
        resp = client.get("/portfolio/positions")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"


def test_protected_route_requires_key():
    with TestClient(create_app()) as client:
        resp = client.get("/config")
        assert resp.status_code == 401
        assert resp.json()["error"] == "unauthorized"


def test_protected_route_rejects_wrong_key():
    with TestClient(create_app()) as client:
        resp = client.get("/config", headers={"X-API-Key": "wrong"})
        assert resp.status_code == 401


def test_protected_route_accepts_correct_key(auth_headers):
    with TestClient(create_app()) as client:
        resp = client.get("/config", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["mode"] == "dry-run"
        # Secrets must never appear in the config summary.
        assert "private_key" not in body
        assert "platform_api_key" not in body
