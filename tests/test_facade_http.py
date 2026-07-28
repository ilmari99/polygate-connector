"""Facade tests over a mocked HTTP transport.

Ports the upstream REST-route coverage to the facade layer: respx intercepts
at the httpx transport, so the real :class:`HttpClient` (retry, error mapping)
and the real query-string construction are exercised without any HTTP server.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from polygate_connector.config import Settings
from polygate_connector.core.errors import UpstreamError, ValidationError
from polygate_connector.services.facade import PolymarketService

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"
DATA = "https://data-api.polymarket.com"


@pytest.fixture
async def service():
    svc = PolymarketService(Settings(http_max_retries=1))
    yield svc
    await svc.aclose()


@respx.mock
async def test_list_markets_wraps_envelope(service):
    route = respx.get(f"{GAMMA}/markets").mock(
        return_value=httpx.Response(200, json=[{"conditionId": "0xabc", "question": "Will it?"}])
    )
    page = await service.list_markets()
    assert route.called
    assert page.source == "gamma"
    assert page.rows[0]["conditionId"] == "0xabc"


@respx.mock
async def test_list_markets_over_limit_is_clamped_to_one_page(service):
    # A limit beyond the server cap is clamped to 100 (one Gamma page); the
    # caller pages on via next_offset instead of receiving a giant response.
    full_page = [{"conditionId": f"0x{i}", "question": "Q"} for i in range(100)]
    route = respx.get(f"{GAMMA}/markets").mock(
        return_value=httpx.Response(200, json=full_page)
    )
    page = await service.list_markets(limit=150)
    assert route.call_count == 1
    assert route.calls[0].request.url.params["limit"] == "100"
    assert len(page.rows) == 100
    assert page.truncated is True
    assert page.next_offset == 100  # full page: more may exist upstream


@respx.mock
async def test_list_markets_decodes_and_compacts(service):
    raw = {
        "conditionId": "0xabc",
        "question": "Will it?",
        "clobTokenIds": "[\"111\",\"222\"]",
        "outcomePrices": "[\"0.6\",\"0.4\"]",
        "description": "lots of noise",
        "image": "https://example.com/i.png",
        "volumeNum": 9.0,
    }
    respx.get(f"{GAMMA}/markets").mock(return_value=httpx.Response(200, json=[raw]))
    # Default is minimal: decoded, low-signal fields and clobTokenIds stripped.
    minimal = (await service.list_markets()).rows[0]
    assert minimal["outcomePrices"] == ["0.6", "0.4"]
    assert "clobTokenIds" not in minimal
    assert "description" not in minimal and "image" not in minimal
    # Compact keeps the token ids, still no noise.
    compact = (await service.list_markets(verbosity="compact")).rows[0]
    assert compact["clobTokenIds"] == ["111", "222"]
    assert compact["volumeNum"] == 9.0
    assert "description" not in compact
    # Full payload on demand: every field kept, still decoded.
    full = (await service.list_markets(verbosity="full")).rows[0]
    assert full["clobTokenIds"] == ["111", "222"]
    assert "description" in full and "image" in full


@respx.mock
async def test_get_event_by_slug(service):
    route = respx.get(f"{GAMMA}/events").mock(
        return_value=httpx.Response(
            200, json=[{"id": "654615", "slug": "fifwc-bra-nor", "gameId": 90086997}]
        )
    )
    env = await service.get_event("fifwc-bra-nor")
    assert route.calls.last.request.url.params["slug"] == "fifwc-bra-nor"
    assert env.data["gameId"] == 90086997


@respx.mock
async def test_get_event_unknown_is_not_found(service):
    from polygate_connector.core.errors import NotFoundError

    respx.get(f"{GAMMA}/events").mock(return_value=httpx.Response(200, json=[]))
    with pytest.raises(NotFoundError):
        await service.get_event("nope")


@respx.mock
async def test_list_events_by_series_id(service):
    route = respx.get(f"{GAMMA}/events").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "slug": "fed-oct"}])
    )
    await service.list_events(series_id=35)
    assert route.calls.last.request.url.params["series_id"] == "35"


@respx.mock
async def test_list_series_is_lightweight_catalog(service):
    # The heavy embedded events[] is replaced with an event_count.
    respx.get(f"{GAMMA}/series").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "35", "slug": "fomc", "events": [{"id": "a"}, {"id": "b"}]}],
        )
    )
    page = await service.list_series()
    s = page.rows[0]
    assert s["slug"] == "fomc"
    assert s["event_count"] == 2
    assert "events" not in s


@respx.mock
async def test_collect_markets_flattens_a_series(service):
    respx.get(f"{GAMMA}/events").mock(
        return_value=httpx.Response(
            200,
            json=[
                {"id": "e1", "slug": "oct", "title": "Oct",
                 "markets": [{"id": "m1", "clobTokenIds": "[\"1\",\"2\"]"}]},
                {"id": "e2", "slug": "sep", "title": "Sep",
                 "markets": [{"id": "m2"}, {"id": "m3"}]},
            ],
        )
    )
    env = await service.collect_markets(series_id=35, verbosity="compact")
    data = env.data
    assert data["scope"] == {"series_id": 35}
    assert data["event_count"] == 2
    assert data["market_count"] == 3
    flat = {m["id"]: m for m in data["markets"]}
    assert flat["m1"]["event_slug"] == "oct"
    assert flat["m1"]["clobTokenIds"] == ["1", "2"]


@respx.mock
async def test_collect_markets_event_expands_by_gameid(service):
    anchor = {
        "id": "654615", "slug": "fifwc-bra-nor", "title": "Brazil vs. Norway",
        "gameId": 90086997, "series": [{"id": "11433"}],
        "markets": [{"id": "m-money"}],
    }
    series_scan = [
        anchor,
        {"id": "654708", "slug": "fifwc-bra-nor-more", "title": "More",
         "gameId": 90086997, "markets": [{"id": "m-ou"}]},
        {"id": "999", "slug": "other", "gameId": 12345, "markets": [{"id": "x"}]},
    ]
    route = respx.get(f"{GAMMA}/events").mock(
        side_effect=[
            httpx.Response(200, json=[anchor]),      # _resolve_event
            httpx.Response(200, json=series_scan),   # _scan_events(series_id=11433)
        ]
    )
    env = await service.collect_markets(
        event="fifwc-bra-nor", group_by="gameId", verbosity="compact"
    )
    data = env.data
    assert route.calls[0].request.url.params["slug"] == "fifwc-bra-nor"
    assert route.calls[1].request.url.params["series_id"] == "11433"
    # Only the two matching-gameId events; the other fixture dropped.
    assert data["event_count"] == 2
    assert {m["id"] for m in data["markets"]} == {"m-money", "m-ou"}
    assert data["scope"]["group_by"] == "gameId"
    assert data["scope"]["match_value"] == 90086997


async def test_collect_markets_requires_exactly_one_scope(service):
    with pytest.raises(ValidationError):
        await service.collect_markets()  # zero scopes
    with pytest.raises(ValidationError):
        await service.collect_markets(series_id=1, tag_id=2)  # two scopes


@respx.mock
async def test_orderbook_routes_to_clob(service):
    respx.get(f"{CLOB}/book").mock(
        return_value=httpx.Response(200, json={"bids": [], "asks": [], "tick_size": "0.01"})
    )
    env = await service.order_book("12345")
    assert env.source == "clob"
    assert env.data["tick_size"] == "0.01"
    # Empty book -> summary present with null fields; no raw ladder by default.
    assert env.data["summary"]["best_bid"] is None
    assert "bids" not in env.data


@respx.mock
async def test_orderbook_summary_computes_best_and_spread(service):
    # Unsorted ladders: best bid is the max bid, best ask is the min ask.
    respx.get(f"{CLOB}/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "tick_size": "0.01",
                "bids": [{"price": "0.48", "size": "10"}, {"price": "0.50", "size": "7"}],
                "asks": [{"price": "0.55", "size": "9"}, {"price": "0.51", "size": "3"}],
            },
        )
    )
    s = (await service.order_book("12345")).data["summary"]
    assert s["best_bid"] == 0.50 and s["best_bid_size"] == "7"
    assert s["best_ask"] == 0.51 and s["best_ask_size"] == "3"
    assert s["midpoint"] == 0.505
    assert s["spread"] == 0.01


@respx.mock
async def test_upstream_error_is_normalised(service):
    respx.get(f"{GAMMA}/markets").mock(return_value=httpx.Response(500, text="boom"))
    with pytest.raises(UpstreamError):
        await service.list_markets()


@respx.mock
async def test_search_routes_to_gamma(service):
    route = respx.get(f"{GAMMA}/public-search").mock(
        return_value=httpx.Response(
            200, json={"events": [{"id": "1"}], "pagination": {"hasMore": False}}
        )
    )
    page = await service.search("bitcoin")
    assert route.called
    assert route.calls.last.request.url.params["q"] == "bitcoin"
    assert page.source == "gamma"
    assert page.rows[0]["id"] == "1"
    assert page.next_page is None  # hasMore false


@respx.mock
async def test_comments_routes_to_gamma(service):
    route = respx.get(f"{GAMMA}/comments").mock(
        return_value=httpx.Response(200, json=[{"id": "c1", "body": "hi"}])
    )
    page = await service.comments(123)
    assert route.called
    params = route.calls.last.request.url.params
    assert params["parent_entity_type"] == "Event"
    assert params["parent_entity_id"] == "123"
    assert params["limit"] == "20"  # default page size
    assert page.source == "gamma"
    assert page.rows[0]["body"] == "hi"


@respx.mock
async def test_holders_routes_to_data_api(service):
    route = respx.get(f"{DATA}/holders").mock(
        return_value=httpx.Response(
            200,
            json=[{"token": "t1", "holders": [
                {"pseudonym": "A", "amount": 7.0, "outcomeIndex": 0, "bio": "x"},
            ]}],
        )
    )
    page = await service.holders("0xabc", limit=5)
    assert route.called
    assert route.calls.last.request.url.params["market"] == "0xabc"
    assert page.source == "data"
    # Flattened to one row per holder, projected to the minimal fields.
    assert page.rows[0] == {"pseudonym": "A", "amount": 7.0, "outcomeIndex": 0}
