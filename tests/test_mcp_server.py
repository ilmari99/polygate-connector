"""Tests for the MCP server wrapper (offline).

These exercise the tool functions directly (the FastMCP ``@tool`` decorator
returns the original coroutine) with a fake service, so nothing touches the
network.
"""

from __future__ import annotations

import pytest

from polygate_connector import mcp_server
from polygate_connector.models.common import ListPage, ResponseEnvelope


class _FakeService:
    """Stands in for PolymarketService: the MCP tools call its operation methods.

    Each method returns a :class:`ResponseEnvelope` exactly like the real facade,
    so the tools' only remaining job - serializing it - is what gets exercised.
    """

    async def list_markets(self, **params):
        return ListPage.of(
            [{"conditionId": "0x1", "question": "Q?", "outcomes": ["Yes", "No"],
              "outcomePrices": ["0.6", "0.4"]}],
            "gamma",
            next_offset=1,
            truncated=True,
        )

    async def search(self, q, **params):
        return ListPage.of(
            [{"id": 7, "title": "Example event", "slug": "example", "market_count": 2}],
            "gamma",
        )

    async def get_event(self, key, **params):
        return ResponseEnvelope.of(
            {"id": "654615", "slug": key, "gameId": 90086997, "markets": [{"id": "m1"}]},
            source="gamma",
        )

    async def list_series(self, **params):
        return ListPage.of([{"id": "35", "slug": "fomc", "event_count": 3}], "gamma")

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


async def test_list_markets_renders_a_table_at_minimal(fake_service):
    result = await mcp_server.list_markets(limit=1)
    assert result["source"] == "gamma"
    assert "fetched_at" in result
    # Minimal verbosity renders rows as a markdown table with the ids intact.
    assert isinstance(result["rows"], str)
    assert result["rows"].startswith("| question |")
    assert "0x1" in result["rows"]
    assert result["next_offset"] == 1


async def test_list_markets_compact_keeps_row_dicts(fake_service):
    result = await mcp_server.list_markets(limit=1, verbosity="compact")
    assert isinstance(result["rows"], list)
    assert result["rows"][0]["conditionId"] == "0x1"


async def test_search_renders_event_rows(fake_service):
    result = await mcp_server.search("example")
    assert result["source"] == "gamma"
    assert isinstance(result["rows"], str)
    assert "Example event" in result["rows"]


async def test_collect_markets_serializes_flat_list(fake_service):
    result = await mcp_server.collect_markets(event="fifwc-bra-nor", group_by="gameId")
    assert result["source"] == "gamma"
    data = result["data"]
    assert data["market_count"] == 2
    assert {m["id"] for m in data["markets"]} == {"m1", "m2"}


async def test_series_tools_serialize(fake_service):
    listed = await mcp_server.list_series()
    assert isinstance(listed["rows"], str)  # minimal default renders a table
    assert "fomc" in listed["rows"]
    ev = await mcp_server.get_event("fifwc-bra-nor")
    assert ev["data"]["gameId"] == 90086997


async def test_lifespan_builds_and_closes_service():
    async with mcp_server._lifespan(mcp_server.mcp):
        assert mcp_server._service is not None
    assert mcp_server._service is None
