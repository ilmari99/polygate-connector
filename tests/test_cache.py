"""Tests for the upstream response cache and its wiring into the facade."""

from __future__ import annotations

import httpx
import respx

from polygate_connector.config import Settings
from polygate_connector.services import cache as cache_module
from polygate_connector.services.cache import TTLCache, ttl_for
from polygate_connector.services.facade import PolymarketService

GAMMA = "https://gamma-api.polymarket.com"


def test_ttl_for_known_and_unknown_paths():
    assert ttl_for("/markets") == 45.0
    assert ttl_for("/public-search") == 300.0
    assert ttl_for("/book") == 15.0
    assert ttl_for("/unknown") == 0.0


def test_ttlcache_expiry_and_eviction(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(cache_module, "_now", lambda: clock[0])
    cache = TTLCache(maxsize=2)
    cache.set("a", 1, ttl=10)
    assert cache.get("a") == 1
    clock[0] = 10.0
    assert TTLCache.is_miss(cache.get("a"))  # expired at exactly ttl
    # Oldest-first eviction at maxsize.
    cache.set("b", 2, ttl=100)
    cache.set("c", 3, ttl=100)
    cache.set("d", 4, ttl=100)
    assert TTLCache.is_miss(cache.get("b"))
    assert cache.get("c") == 3 and cache.get("d") == 4
    # A non-positive ttl stores nothing.
    cache.set("e", 5, ttl=0)
    assert TTLCache.is_miss(cache.get("e"))


@respx.mock
async def test_repeat_read_hits_cache_not_upstream():
    service = PolymarketService(Settings(http_max_retries=1))
    route = respx.get(f"{GAMMA}/markets").mock(
        return_value=httpx.Response(200, json=[{"conditionId": "0x1", "question": "Q"}])
    )
    try:
        first = await service.list_markets()
        second = await service.list_markets()
    finally:
        await service.aclose()
    assert route.call_count == 1  # second call served from cache
    assert first.rows == second.rows


@respx.mock
async def test_different_params_are_cached_separately():
    service = PolymarketService(Settings(http_max_retries=1))
    route = respx.get(f"{GAMMA}/markets").mock(
        return_value=httpx.Response(200, json=[{"conditionId": "0x1", "question": "Q"}])
    )
    try:
        await service.list_markets(limit=5)
        await service.list_markets(limit=6)
    finally:
        await service.aclose()
    assert route.call_count == 2


@respx.mock
async def test_errors_are_not_cached():
    service = PolymarketService(Settings(http_max_retries=1))
    route = respx.get(f"{GAMMA}/markets").mock(
        side_effect=[
            httpx.Response(500, text="boom"),
            httpx.Response(200, json=[{"conditionId": "0x1", "question": "Q"}]),
        ]
    )
    try:
        from polygate_connector.core.errors import UpstreamError

        import pytest

        with pytest.raises(UpstreamError):
            await service.list_markets()
        page = await service.list_markets()  # retried for real, not a cached error
    finally:
        await service.aclose()
    assert route.call_count == 2
    assert page.rows[0]["conditionId"] == "0x1"
