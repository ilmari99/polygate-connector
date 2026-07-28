"""Tests for the public HTTP entry point: health, transport security, MCP round-trip."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from polygate_connector import serve
from polygate_connector.config import Settings
from polygate_connector.core.errors import ConfigurationError

PUBLIC_HOST = "mcp.example.test"


def _settings(**overrides) -> Settings:
    return Settings(public_host=PUBLIC_HOST, **overrides)


def _client() -> TestClient:
    app = serve.create_app(_settings())
    return TestClient(app, base_url=f"http://{PUBLIC_HOST}")


def test_missing_public_host_fails_fast():
    with pytest.raises(ConfigurationError):
        serve.create_app(Settings(public_host=None))


def test_healthz_reports_ok():
    with _client() as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"


def test_mcp_initialize_round_trips():
    with _client() as client:
        resp = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-03-26",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
            headers={
                "Accept": "application/json, text/event-stream",
                "Origin": f"https://{PUBLIC_HOST}",
            },
        )
        assert resp.status_code == 200
        assert "polymarket-research" in resp.text


def test_wrong_host_header_is_rejected():
    app = serve.create_app(_settings())
    with TestClient(app, base_url="http://evil.example.com") as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={"Accept": "application/json, text/event-stream"},
        )
        assert resp.status_code in (400, 403, 421)


def test_wrong_origin_is_rejected():
    with _client() as client:
        resp = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
            headers={
                "Accept": "application/json, text/event-stream",
                "Origin": "https://evil.example.com",
            },
        )
        assert resp.status_code in (400, 403, 421)


def test_rate_limit_kicks_in():
    app = serve.create_app(_settings(rate_limit_per_ip="3/minute"))
    with TestClient(app, base_url=f"http://{PUBLIC_HOST}") as client:
        codes = [client.get("/healthz").status_code for _ in range(5)]
    assert codes[:3] == [200, 200, 200]
    assert 429 in codes[3:]


def test_client_ip_respects_proxy_trust_setting():
    from types import SimpleNamespace

    request = SimpleNamespace(
        headers={"CF-Connecting-IP": "203.0.113.9"},
        client=SimpleNamespace(host="127.0.0.1"),
    )
    trusted = serve._client_ip_factory(trust_proxy_headers=True)
    untrusted = serve._client_ip_factory(trust_proxy_headers=False)
    assert trusted(request) == "203.0.113.9"
    assert untrusted(request) == "127.0.0.1"
