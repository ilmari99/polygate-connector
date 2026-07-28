"""Per-tool payload budgets over recorded upstream fixtures.

Each fixture in tests/fixtures/ is a real upstream response snapshot captured
by scripts/measure_payloads.py. Replaying them through the actual tools pins
the serialized default-argument response size, so a change that silently
re-bloats a payload fails here instead of surfacing in review.
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import respx

from polygate_connector import mcp_server
from polygate_connector.config import Settings
from polygate_connector.constants import RESPONSE_MAX_BYTES
from polygate_connector.render import payload_bytes
from polygate_connector.services.facade import PolymarketService

FIXTURES = Path(__file__).parent / "fixtures"

# tool -> (max serialized bytes for a default-argument call)
BUDGETS = {
    "list_markets": 5_000,
    "list_events": 30_000,
    "search": 5_000,
    "list_series": 4_000,
    "list_tags": 6_000,
    "get_market": 3_000,
    "get_event": 15_000,
    "collect_markets": 8_000,
    "get_comments": 8_000,
    "get_holders": 4_000,
    "get_order_book": 3_000,
    "get_last_trade_price": 500,
    "get_prices_history": 1_000,
}


def _records(tool: str) -> list[dict]:
    return json.loads((FIXTURES / f"{tool}.json").read_text())


def _mock(records: list[dict]) -> None:
    by_url: dict[str, list] = {}
    for rec in records:
        by_url.setdefault(rec["url"], []).append(rec["response"])
    for url, responses in by_url.items():
        respx.get(url).mock(
            side_effect=[httpx.Response(200, json=r) for r in responses]
        )


def _param(records: list[dict], key: str) -> str:
    value = records[0]["params"].get(key)
    assert value is not None, f"fixture lacks param {key!r}"
    return str(value)


async def _call(tool: str, records: list[dict]):
    if tool == "list_markets":
        return await mcp_server.list_markets()
    if tool == "list_events":
        return await mcp_server.list_events()
    if tool == "search":
        return await mcp_server.search(_param(records, "q"))
    if tool == "list_series":
        return await mcp_server.list_series()
    if tool == "list_tags":
        return await mcp_server.list_tags()
    if tool == "get_market":
        return await mcp_server.get_market(_param(records, "condition_ids"))
    if tool == "get_event":
        return await mcp_server.get_event(_param(records, "slug"))
    if tool == "collect_markets":
        return await mcp_server.collect_markets(event=_param(records, "slug"))
    if tool == "get_comments":
        return await mcp_server.get_comments(int(_param(records, "parent_entity_id")))
    if tool == "get_holders":
        return await mcp_server.get_holders(_param(records, "market"))
    if tool == "get_order_book":
        return await mcp_server.get_order_book(_param(records, "token_id"))
    if tool == "get_last_trade_price":
        return await mcp_server.get_last_trade_price(_param(records, "token_id"))
    if tool == "get_prices_history":
        return await mcp_server.get_prices_history(_param(records, "market"))
    raise AssertionError(tool)


@pytest.mark.parametrize("tool", sorted(BUDGETS))
async def test_default_call_stays_in_budget(tool):
    records = _records(tool)
    service = PolymarketService(Settings(http_max_retries=1))
    previous = mcp_server._service
    mcp_server._service = service
    try:
        with respx.mock:
            _mock(records)
            result = await _call(tool, records)
    finally:
        mcp_server._service = previous
        await service.aclose()
    assert "error" not in result, result.get("detail")
    size = payload_bytes(result)
    assert size <= BUDGETS[tool], f"{tool}: {size} bytes > budget {BUDGETS[tool]}"
    assert size <= RESPONSE_MAX_BYTES


async def test_health_is_tiny():
    result = await mcp_server.health()
    assert payload_bytes(result) < 1_000
