"""Tests for market-data and trading routes with mocked upstreams."""

from __future__ import annotations

import httpx
import respx
from fastapi.testclient import TestClient

from polygate.main import create_app


@respx.mock
def test_list_markets_wraps_envelope(auth_headers):
    route = respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(200, json=[{"conditionId": "0xabc", "question": "Will it?"}])
    )
    with TestClient(create_app()) as client:
        resp = client.get("/markets", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert route.called
        assert body["source"] == "gamma"
        assert "fetched_at" in body
        assert body["data"][0]["conditionId"] == "0xabc"


@respx.mock
def test_orderbook_routes_to_clob(auth_headers):
    respx.get("https://clob.polymarket.com/book").mock(
        return_value=httpx.Response(200, json={"bids": [], "asks": [], "tick_size": "0.01"})
    )
    with TestClient(create_app()) as client:
        resp = client.get("/orderbook/12345", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert body["source"] == "clob"
        assert body["data"]["tick_size"] == "0.01"


@respx.mock
def test_upstream_error_is_normalised(auth_headers):
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with TestClient(create_app()) as client:
        resp = client.get("/markets", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["error"] == "gamma_error"


def test_place_order_dry_run_returns_simulated(auth_headers):
    with TestClient(create_app()) as client:
        resp = client.post(
            "/orders",
            headers=auth_headers,
            json={"token_id": "123", "side": "BUY", "size": 5, "price": 0.5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["simulated"] is True
        assert body["status"] == "SIMULATED"


def test_place_order_requires_auth():
    with TestClient(create_app()) as client:
        resp = client.post(
            "/orders", json={"token_id": "123", "side": "BUY", "size": 5, "price": 0.5}
        )
        assert resp.status_code == 401


def test_validation_error_uses_error_envelope(auth_headers):
    with TestClient(create_app()) as client:
        resp = client.post(
            "/orders",
            headers=auth_headers,
            json={"token_id": "123", "side": "BUY", "size": 5, "price": 1.5},
        )
        assert resp.status_code == 422
        body = resp.json()
        assert body["error"] == "validation_error"
        assert "price" in body["detail"]
