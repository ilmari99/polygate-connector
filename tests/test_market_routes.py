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
def test_list_markets_pages_past_gamma_cap(auth_headers):
    # Gamma caps a page at 100 rows; the facade must fan out to fetch more.
    page1 = [{"conditionId": f"0x{i}", "question": "Q"} for i in range(100)]
    page2 = [{"conditionId": f"0x{i}", "question": "Q"} for i in range(100, 130)]
    route = respx.get("https://gamma-api.polymarket.com/markets").mock(
        side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)]
    )
    with TestClient(create_app()) as client:
        resp = client.get("/markets", params={"limit": 150}, headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        # Two upstream calls; a short second page stops the loop at 130 rows.
        assert route.call_count == 2
        assert len(body["data"]) == 130
        assert route.calls[0].request.url.params["limit"] == "100"
        assert route.calls[0].request.url.params["offset"] == "0"
        assert route.calls[1].request.url.params["offset"] == "100"


@respx.mock
def test_list_markets_decodes_and_compacts(auth_headers):
    raw = {
        "conditionId": "0xabc",
        "question": "Will it?",
        "clobTokenIds": "[\"111\",\"222\"]",
        "outcomePrices": "[\"0.6\",\"0.4\"]",
        "description": "lots of noise",
        "image": "https://example.com/i.png",
        "volumeNum": 9.0,
    }
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(200, json=[raw])
    )
    with TestClient(create_app()) as client:
        # Default is now compact: JSON fields decoded AND low-signal fields stripped.
        compact = client.get("/markets", headers=auth_headers).json()["data"][0]
        assert compact["clobTokenIds"] == ["111", "222"]
        assert compact["outcomePrices"] == ["0.6", "0.4"]
        assert compact["volumeNum"] == 9.0
        assert "description" not in compact and "image" not in compact
        # Full payload on demand: compact=false keeps every field, still decoded.
        full = client.get(
            "/markets", params={"compact": "false"}, headers=auth_headers
        ).json()["data"][0]
        assert full["clobTokenIds"] == ["111", "222"]
        assert "description" in full and "image" in full



@respx.mock
def test_get_event_by_slug(auth_headers):
    route = respx.get("https://gamma-api.polymarket.com/events").mock(
        return_value=httpx.Response(
            200, json=[{"id": "654615", "slug": "fifwc-bra-nor", "gameId": 90086997}]
        )
    )
    with TestClient(create_app()) as client:
        resp = client.get("/events/fifwc-bra-nor", headers=auth_headers)
        assert resp.status_code == 200
        body = resp.json()
        assert route.calls.last.request.url.params["slug"] == "fifwc-bra-nor"
        assert body["data"]["gameId"] == 90086997


@respx.mock
def test_get_event_unknown_is_not_found(auth_headers):
    respx.get("https://gamma-api.polymarket.com/events").mock(
        return_value=httpx.Response(200, json=[])
    )
    with TestClient(create_app()) as client:
        resp = client.get("/events/nope", headers=auth_headers)
        assert resp.status_code == 404
        assert resp.json()["error"] == "not_found"


@respx.mock
def test_list_events_by_series_id(auth_headers):
    route = respx.get("https://gamma-api.polymarket.com/events").mock(
        return_value=httpx.Response(200, json=[{"id": "1", "slug": "fed-oct"}])
    )
    with TestClient(create_app()) as client:
        resp = client.get("/events", params={"series_id": 35}, headers=auth_headers)
        assert resp.status_code == 200
        assert route.calls.last.request.url.params["series_id"] == "35"


@respx.mock
def test_list_series_is_lightweight_catalog(auth_headers):
    # The heavy embedded events[] is replaced with an event_count.
    respx.get("https://gamma-api.polymarket.com/series").mock(
        return_value=httpx.Response(
            200,
            json=[{"id": "35", "slug": "fomc", "events": [{"id": "a"}, {"id": "b"}]}],
        )
    )
    with TestClient(create_app()) as client:
        body = client.get("/series", headers=auth_headers).json()
        s = body["data"][0]
        assert s["slug"] == "fomc"
        assert s["event_count"] == 2
        assert "events" not in s


@respx.mock
def test_collect_markets_flattens_a_series(auth_headers):
    respx.get("https://gamma-api.polymarket.com/events").mock(
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
    with TestClient(create_app()) as client:
        body = client.get(
            "/collect-markets", params={"series_id": 35}, headers=auth_headers
        ).json()
        data = body["data"]
        assert data["scope"] == {"series_id": 35}
        assert data["event_count"] == 2
        assert data["market_count"] == 3
        flat = {m["id"]: m for m in data["markets"]}
        assert flat["m1"]["event_slug"] == "oct"
        assert flat["m1"]["clobTokenIds"] == ["1", "2"]


@respx.mock
def test_collect_markets_event_expands_by_gameid(auth_headers):
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
    route = respx.get("https://gamma-api.polymarket.com/events").mock(
        side_effect=[
            httpx.Response(200, json=[anchor]),      # _resolve_event
            httpx.Response(200, json=series_scan),   # _scan_events(series_id=11433)
        ]
    )
    with TestClient(create_app()) as client:
        body = client.get(
            "/collect-markets",
            params={"event": "fifwc-bra-nor", "group_by": "gameId"},
            headers=auth_headers,
        ).json()
        data = body["data"]
        assert route.calls[0].request.url.params["slug"] == "fifwc-bra-nor"
        assert route.calls[1].request.url.params["series_id"] == "11433"
        # Only the two matching-gameId events; the other fixture dropped.
        assert data["event_count"] == 2
        assert {m["id"] for m in data["markets"]} == {"m-money", "m-ou"}
        assert data["scope"]["group_by"] == "gameId"
        assert data["scope"]["match_value"] == 90086997


def test_collect_markets_requires_exactly_one_scope(auth_headers):
    with TestClient(create_app()) as client:
        # zero scopes
        r0 = client.get("/collect-markets", headers=auth_headers)
        assert r0.status_code == 422 and r0.json()["error"] == "validation_error"
        # two scopes
        r2 = client.get(
            "/collect-markets", params={"series_id": 1, "tag_id": 2}, headers=auth_headers
        )
        assert r2.status_code == 422 and r2.json()["error"] == "validation_error"


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
        # Empty book -> summary present with null fields.
        assert body["data"]["summary"]["best_bid"] is None


@respx.mock
def test_orderbook_summary_computes_best_and_spread(auth_headers):
    # Unsorted ladders: best bid is the max bid, best ask is the min ask.
    respx.get("https://clob.polymarket.com/book").mock(
        return_value=httpx.Response(
            200,
            json={
                "tick_size": "0.01",
                "bids": [{"price": "0.48", "size": "10"}, {"price": "0.50", "size": "7"}],
                "asks": [{"price": "0.55", "size": "9"}, {"price": "0.51", "size": "3"}],
            },
        )
    )
    with TestClient(create_app()) as client:
        s = client.get("/orderbook/12345", headers=auth_headers).json()["data"]["summary"]
        assert s["best_bid"] == 0.50 and s["best_bid_size"] == "7"
        assert s["best_ask"] == 0.51 and s["best_ask_size"] == "3"
        assert s["midpoint"] == 0.505
        assert s["spread"] == 0.01


@respx.mock
def test_upstream_error_is_normalised(auth_headers):
    respx.get("https://gamma-api.polymarket.com/markets").mock(
        return_value=httpx.Response(500, text="boom")
    )
    with TestClient(create_app()) as client:
        resp = client.get("/markets", headers=auth_headers)
        assert resp.status_code == 502
        assert resp.json()["error"] == "upstream_error"


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
def test_search_flatten_is_opt_in(auth_headers):
    respx.get("https://gamma-api.polymarket.com/public-search").mock(
        return_value=httpx.Response(
            200,
            json={
                "events": [
                    {
                        "id": "42",
                        "title": "Will it rain?",
                        "markets": [
                            {"id": "m1", "clobTokenIds": "[\"111\",\"222\"]"},
                        ],
                    }
                ],
                "pagination": {"hasMore": False},
            },
        )
    )
    with TestClient(create_app()) as client:
        # Default: no flat top-level markets array; nested markets still decoded.
        default = client.get(
            "/search", params={"q": "rain"}, headers=auth_headers
        ).json()["data"]
        assert "markets" not in default
        assert default["events"][0]["markets"][0]["clobTokenIds"] == ["111", "222"]
        # Opt-in: flatten=true synthesizes the tagged flat array.
        flat = client.get(
            "/search", params={"q": "rain", "flatten": "true"}, headers=auth_headers
        ).json()["data"]["markets"]
        assert len(flat) == 1
        assert flat[0]["id"] == "m1"
        assert flat[0]["clobTokenIds"] == ["111", "222"]
        assert flat[0]["event_id"] == "42"
        assert flat[0]["event_title"] == "Will it rain?"


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
