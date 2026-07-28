"""Every failure mode must surface as a typed, actionable error dict.

Connector review rejects generic 500s and raw tracebacks: a tool called with
bad input has to return a stable error code and a message that says what to
do next.
"""

from __future__ import annotations

import httpx
import respx

from polygate_connector import mcp_server
from polygate_connector.config import Settings
from polygate_connector.services.facade import PolymarketService

GAMMA = "https://gamma-api.polymarket.com"


class _ExplodingService:
    """A service whose operations raise a non-PlatformError."""

    def __getattr__(self, name):
        async def _boom(*args, **kwargs):
            raise RuntimeError("secret internal state: /home/user/.env")

        return _boom


async def _with_service(service):
    previous = mcp_server._service
    mcp_server._service = service
    return previous


async def test_not_found_returns_code_and_next_step():
    service = PolymarketService(Settings(http_max_retries=1))
    previous = await _with_service(service)
    try:
        with respx.mock:
            respx.get(f"{GAMMA}/markets").mock(
                return_value=httpx.Response(200, json=[])
            )
            result = await mcp_server.get_market("0xdeadbeef")
    finally:
        await service.aclose()
        mcp_server._service = previous
    assert result["error"] == "not_found"
    # The detail tells the model where to get a valid id.
    assert "search" in result["detail"] or "list_markets" in result["detail"]


async def test_upstream_500_returns_upstream_error_with_status():
    service = PolymarketService(Settings(http_max_retries=1))
    previous = await _with_service(service)
    try:
        with respx.mock:
            respx.get(f"{GAMMA}/markets").mock(
                return_value=httpx.Response(500, text="boom")
            )
            result = await mcp_server.list_markets()
    finally:
        await service.aclose()
        mcp_server._service = previous
    assert result["error"] == "upstream_error"
    assert "500" in result["detail"]


async def test_validation_error_names_the_problem():
    service = PolymarketService(Settings(http_max_retries=1))
    previous = await _with_service(service)
    try:
        result = await mcp_server.collect_markets()  # no scope given
    finally:
        await service.aclose()
        mcp_server._service = previous
    assert result["error"] == "validation_error"
    assert "exactly one" in result["detail"]


async def test_unexpected_exception_is_sanitised():
    previous = await _with_service(_ExplodingService())
    try:
        result = await mcp_server.list_markets()
    finally:
        mcp_server._service = previous
    assert result["error"] == "internal_error"
    # The raw exception message (which may leak paths/state) must not pass through.
    assert "secret internal state" not in result["detail"]
    assert ".env" not in result["detail"]


async def test_health_never_needs_the_service():
    # health must succeed with no service configured at all.
    result = await mcp_server.health()
    assert result["status"] == "ok"
