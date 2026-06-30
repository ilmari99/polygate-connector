"""Tests for the MCP server wrapper (offline).

These exercise the tool functions directly (the FastMCP ``@tool`` decorator
returns the original coroutine) with a fake service for reads and the real
dry-run facade for the trading path, so nothing touches the network.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from polygate import mcp_server
from polygate.config import get_settings
from polygate.services.facade import PolymarketService


class _FakeGamma:
    async def list_markets(self, **params):
        return [{"conditionId": "0x1", "clobTokenIds": "[\"111\", \"222\"]"}]

    async def get_market_by_slug(self, slug):
        return [{"slug": slug}]

    async def search(self, query, **params):
        return {
            "events": [
                {
                    "id": 7,
                    "title": "Example event",
                    "markets": [{"clobTokenIds": "[\"111\"]"}],
                }
            ]
        }


@pytest.fixture
def fake_service():
    previous = mcp_server._service
    mcp_server._service = SimpleNamespace(gamma=_FakeGamma())
    try:
        yield mcp_server._service
    finally:
        mcp_server._service = previous


async def test_health_tool_reports_mode():
    result = await mcp_server.health()
    assert result["status"] == "ok"
    assert result["mode"] == "dry-run"  # conftest sets DRY_RUN=true
    assert "configured" in result


async def test_list_markets_wraps_envelope(fake_service):
    result = await mcp_server.list_markets(limit=1)
    assert result["source"] == "gamma"
    assert "fetched_at" in result
    assert result["data"][0]["conditionId"] == "0x1"


async def test_search_flattens_markets(fake_service):
    result = await mcp_server.search("example")
    assert result["source"] == "gamma"
    markets = result["data"]["markets"]
    assert markets and markets[0]["event_id"] == 7
    assert markets[0]["event_title"] == "Example event"


async def test_place_order_validation_error(fake_service):
    # GTC order without a price is rejected before any upstream call.
    result = await mcp_server.place_order(token_id="111", side="BUY", size=5)
    assert result["error"] == "validation_error"


async def test_place_order_dry_run_is_simulated():
    service = PolymarketService(get_settings())
    previous = mcp_server._service
    mcp_server._service = service
    try:
        result = await mcp_server.place_order(
            token_id="111", side="BUY", size=5, price=0.42
        )
        assert result["simulated"] is True
        assert result["success"] is True
        assert result["status"] == "SIMULATED"
    finally:
        await service.aclose()
        mcp_server._service = previous


async def test_lifespan_builds_and_closes_service(monkeypatch):
    # Keep dry-run so onboarding is skipped and no network is touched.
    monkeypatch.setenv("DRY_RUN", "true")
    async with mcp_server._lifespan(mcp_server.mcp):
        assert mcp_server._service is not None
    assert mcp_server._service is None
