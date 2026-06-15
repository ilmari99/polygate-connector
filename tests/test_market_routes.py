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


@respx.mock
def test_search_routes_to_gamma(auth_headers):
    route = respx.get("https://gamma-api.polymarket.com/public-search").mock(
        return_value=httpx.Response(
            200, json={"events": [{"id": "1"}], "pagination": {"hasMore": False}}
        )
    )
    with TestClient(create_app()) as client:
        resp = client.get("/search", params={"q": "bitcoin"}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert route.called
        assert route.calls.last.request.url.params["q"] == "bitcoin"
        assert body["source"] == "gamma"
        assert body["data"]["events"][0]["id"] == "1"


@respx.mock
def test_comments_routes_to_gamma(auth_headers):
    route = respx.get("https://gamma-api.polymarket.com/comments").mock(
        return_value=httpx.Response(200, json=[{"id": "c1", "body": "hi"}])
    )
    with TestClient(create_app()) as client:
        resp = client.get("/comments", params={"event_id": 123}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert route.called
        params = route.calls.last.request.url.params
        assert params["parent_entity_type"] == "Event"
        assert params["parent_entity_id"] == "123"
        assert body["source"] == "gamma"
        assert body["data"][0]["id"] == "c1"


@respx.mock
def test_holders_routes_to_data_api(auth_headers):
    route = respx.get("https://data-api.polymarket.com/holders").mock(
        return_value=httpx.Response(200, json=[{"token": "t1", "holders": []}])
    )
    with TestClient(create_app()) as client:
        resp = client.get("/holders/0xabc", params={"limit": 5}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert route.called
        assert route.calls.last.request.url.params["market"] == "0xabc"
        assert body["source"] == "data"
        assert body["data"][0]["token"] == "t1"


@respx.mock
def test_comments_is_public():
    respx.get("https://gamma-api.polymarket.com/comments").mock(
        return_value=httpx.Response(200, json=[{"id": "c1", "body": "hi"}])
    )
    with TestClient(create_app()) as client:
        # Research endpoints require no X-API-Key.
        resp = client.get("/comments", params={"event_id": 1})
        assert resp.status_code == 200
        assert resp.json()["source"] == "gamma"
