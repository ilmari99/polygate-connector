"""Tests for the MCP server wrapper (offline).

These exercise the tool functions directly (the FastMCP ``@tool`` decorator
returns the original coroutine) with a fake service, so nothing touches the
network.
"""

from __future__ import annotations

import pytest

from polygate_connector import mcp_server
from polygate_connector.models.common import ResponseEnvelope


class _FakeService:
    """Stands in for PolymarketService: the MCP tools call its operation methods.

    Each method returns a :class:`ResponseEnvelope` exactly like the real facade,
    so the tools' only remaining job - serializing it - is what gets exercised.
    """

    async def list_markets(self, **params):
        return ResponseEnvelope.of(
            [{"conditionId": "0x1", "clobTokenIds": "[\"111\", \"222\"]"}], source="gamma"
        )

    async def search(self, q, **params):
        # The facade does the flattening; here we return an already-flat envelope.
        return ResponseEnvelope.of(
            {
                "events": [{"id": 7, "title": "Example event"}],
                "markets": [
                    {"clobTokenIds": "[\"111\"]", "event_id": 7, "event_title": "Example event"}
                ],
            },
            source="gamma",
        )

    async def get_event(self, key, **params):
        return ResponseEnvelope.of(
            {"id": "654615", "slug": key, "gameId": 90086997, "markets": [{"id": "m1"}]},
            source="gamma",
        )

    async def list_series(self, **params):
        return ResponseEnvelope.of([{"id": "35", "slug": "fomc", "event_count": 3}], source="gamma")

    async def collect_markets(self, **params):
        return ResponseEnvelope.of(
            {"scope": params, "event_count": 2, "market_count": 2,
             "markets": [{"id": "m1", "event_slug": "a"}, {"id": "m2", "event_slug": "b"}]},
            source="gamma",
        )


@pytest.fixture
def fake_service():
    previous = mcp_server._service
    mcp_server._service = _FakeService()
    try:
        yield mcp_server._service
    finally:
        mcp_server._service = previous


async def test_health_tool_reports_status_and_hosts():
    result = await mcp_server.health()
    assert result["status"] == "ok"
    assert result["server"] == mcp_server.mcp.name
    assert result["hosts"]["gamma"].startswith("https://")
    # No wallet/trading state exists to leak.
    assert "wallet_address" not in result
    assert "can_trade_live" not in result


async def test_list_markets_wraps_envelope(fake_service):
    result = await mcp_server.list_markets(limit=1)
    assert result["source"] == "gamma"
    assert "fetched_at" in result
    assert result["data"][0]["conditionId"] == "0x1"


async def test_search_serializes_flattened_envelope(fake_service):
    result = await mcp_server.search("example")
    assert result["source"] == "gamma"
    markets = result["data"]["markets"]
    assert markets and markets[0]["event_id"] == 7
    assert markets[0]["event_title"] == "Example event"


async def test_collect_markets_serializes_flat_list(fake_service):
    result = await mcp_server.collect_markets(event="fifwc-bra-nor", group_by="gameId")
    assert result["source"] == "gamma"
    data = result["data"]
    assert data["market_count"] == 2
    assert {m["id"] for m in data["markets"]} == {"m1", "m2"}


async def test_series_tools_serialize(fake_service):
    listed = await mcp_server.list_series()
    assert listed["data"][0]["event_count"] == 3
    ev = await mcp_server.get_event("fifwc-bra-nor")
    assert ev["data"]["gameId"] == 90086997


async def test_lifespan_builds_and_closes_service():
    async with mcp_server._lifespan(mcp_server.mcp):
        assert mcp_server._service is not None
    assert mcp_server._service is None
