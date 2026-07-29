"""Tests for the shared facade operations layer (HTTP boundary stubbed)."""

from __future__ import annotations

import pytest

from polygate_connector.config import get_settings
from polygate_connector.core.errors import NotFoundError
from polygate_connector.services.facade import PolymarketService, _flatten_search


def _service(monkeypatch, read_return):
    """Build a facade whose HTTP boundary (`_read`) is stubbed.

    ``read_return`` is either a fixed value or a callable ``(params) -> value``.
    The stub also records the last params it was called with on ``svc._last_params``.
    """
    svc = PolymarketService(get_settings())

    async def fake_read(host, path, source, params=None):
        svc._last_params = params or {}
        return read_return(params or {}) if callable(read_return) else read_return

    monkeypatch.setattr(svc, "_read", fake_read)
    return svc


def test_flatten_search_adds_event_context():
    data = {
        "events": [
            {
                "id": "42",
                "title": "Will it rain?",
                "markets": [{"id": "m1", "clobTokenIds": "[\"111\",\"222\"]"}],
            }
        ],
        "pagination": {"hasMore": False},
    }
    result = _flatten_search(data)
    markets = result["markets"]
    assert len(markets) == 1
    assert markets[0]["id"] == "m1"
    assert markets[0]["event_id"] == "42"
    assert markets[0]["event_title"] == "Will it rain?"
    # The original events list is preserved alongside the flat view.
    assert result["events"][0]["id"] == "42"


def test_flatten_search_passthrough_when_already_flat():
    data = {"markets": [{"id": "m1"}], "events": []}
    assert _flatten_search(data) is data


def test_flatten_search_ignores_non_dict():
    assert _flatten_search([1, 2, 3]) == [1, 2, 3]


# --- get_market: single object + not_found (issue #3) ---


async def test_get_market_unwraps_single_object(monkeypatch):
    svc = _service(monkeypatch, [
        {"conditionId": "0xabc", "clobTokenIds": "[\"1\",\"2\"]",
         "description": "Resolves YES if...", "resolutionSource": "https://example.com"}
    ])
    env = await svc.get_market("0xabc")
    assert isinstance(env.data, dict)  # NOT a 1-element list
    assert env.data["conditionId"] == "0xabc"
    assert env.data["clobTokenIds"] == ["1", "2"]  # decoded, kept at compact
    # The detail call keeps the resolution criteria on top of the compact
    # projection (list projections still drop them).
    assert env.data["description"] == "Resolves YES if..."
    assert env.data["resolutionSource"] == "https://example.com"
    await svc.aclose()


async def test_get_market_minimal_still_drops_description(monkeypatch):
    svc = _service(monkeypatch, [{"conditionId": "0xabc", "description": "long"}])
    env = await svc.get_market("0xabc", verbosity="minimal")
    assert "description" not in env.data
    await svc.aclose()


# --- sort aliasing: Gamma sorts string columns lexicographically ---


async def test_list_markets_aliases_string_sort_columns(monkeypatch):
    svc = _service(monkeypatch, [])
    await svc.list_markets(order="liquidity")
    assert svc._last_params["order"] == "liquidityNum"
    await svc.list_markets(order="volume")
    assert svc._last_params["order"] == "volumeNum"
    await svc.list_markets(order="volume24hr")  # numeric column passes through
    assert svc._last_params["order"] == "volume24hr"
    await svc.list_markets()  # no order requested -> none sent
    assert svc._last_params.get("order") is None
    await svc.aclose()


# --- list_tags: explicit paging over the tag catalog ---


async def test_list_tags_pages_with_explicit_limit(monkeypatch):
    svc = _service(monkeypatch, lambda params: [
        {"id": str(i), "label": f"t{i}", "slug": f"t{i}"}
        for i in range(params.get("limit", 0))
    ])
    page = await svc.list_tags(limit=2, offset=4)
    assert svc._last_params["limit"] == 2
    assert svc._last_params["offset"] == 4
    # Sort pinned to id: Gamma's default order is arbitrary, and an unstable
    # order across offset pages could silently skip or duplicate tags.
    assert svc._last_params["order"] == "id"
    assert svc._last_params["ascending"] is True
    assert page.returned == 2
    assert page.next_offset == 6  # full page -> more may exist
    await svc.aclose()


async def test_get_market_not_found_raises(monkeypatch):
    svc = _service(monkeypatch, [])
    with pytest.raises(NotFoundError):
        await svc.get_market("0xdeadbeef")
    await svc.aclose()


async def test_get_market_full_when_requested(monkeypatch):
    svc = _service(monkeypatch, [{"conditionId": "0xabc", "description": "keep"}])
    env = await svc.get_market("0xabc", verbosity="full")
    assert env.data["description"] == "keep"
    await svc.aclose()


# --- limit clamping ---


async def test_list_markets_clamps_limit_and_flags_truncation(monkeypatch):
    svc = _service(monkeypatch, lambda params: [
        {"conditionId": f"0x{i}", "question": "Q"} for i in range(params.get("limit", 0))
    ])
    page = await svc.list_markets(limit=5000)
    assert svc._last_params["limit"] == 100  # clamped before the upstream call
    assert page.returned == 100
    assert page.truncated is True
    assert page.next_offset == 100
    await svc.aclose()


async def test_list_markets_short_page_ends_pagination(monkeypatch):
    svc = _service(monkeypatch, [{"conditionId": "0x1", "question": "Q"}])
    page = await svc.list_markets(limit=10)
    assert page.returned == 1
    assert page.next_offset is None
    assert page.truncated is False
    await svc.aclose()


# --- prices_history default window (issue #5) ---


async def test_prices_history_defaults_to_one_week(monkeypatch):
    svc = _service(monkeypatch, {"history": []})
    env = await svc.prices_history("token1")
    assert svc._last_params["interval"] == "1w"  # defaulted upstream call
    assert svc._last_params["fidelity"] == 60  # a wide range needs a fidelity floor
    assert env.data["interval"] == "1w"  # echoed back to the caller
    await svc.aclose()


async def test_prices_history_explicit_window_not_defaulted(monkeypatch):
    svc = _service(monkeypatch, {"history": []})
    env = await svc.prices_history("token1", start_ts=1, end_ts=2)
    assert svc._last_params["interval"] is None  # no interval injected
    assert "interval" not in env.data  # not echoed for window mode
    await svc.aclose()


async def test_prices_history_summarizes_by_default(monkeypatch):
    svc = _service(monkeypatch, {"history": [{"t": 1, "p": 0.5}, {"t": 2, "p": 0.7}]})
    env = await svc.prices_history("token1", interval="1d")
    assert "history" not in env.data
    assert env.data["summary"]["change"] == 0.2
    assert env.data["interval"] == "1d"
    await svc.aclose()


async def test_prices_history_raw_returns_points(monkeypatch):
    svc = _service(monkeypatch, {"history": [{"t": 1, "p": 0.5}]})
    env = await svc.prices_history("token1", interval="1d", raw=True)
    assert env.data["history"] == [{"t": 1, "p": 0.5}]
    await svc.aclose()


# --- search rows ---


def _search_payload(_params):
    return {
        "events": [
            {"id": "7", "title": "T", "slug": "s",
             "markets": [{"id": "m1", "clobTokenIds": "[\"1\"]", "liquidityNum": 3.0}]}
        ],
        "pagination": {"hasMore": True},
    }


async def test_search_minimal_rows_are_event_summaries(monkeypatch):
    svc = _service(monkeypatch, _search_payload)
    page = await svc.search("q")
    assert svc._last_params["limit_per_type"] == 10  # default applied
    row = page.rows[0]
    assert row["title"] == "T"
    assert row["market_count"] == 1
    assert row["top_markets"][0]["liquidityNum"] == 3.0
    assert page.next_page == 2  # upstream said hasMore
    assert page.truncated is False  # more-exists is next_page's job, not truncated's
    await svc.aclose()


async def test_search_full_keeps_raw_events(monkeypatch):
    svc = _service(monkeypatch, _search_payload)
    page = await svc.search("q", verbosity="full")
    # Full rows keep nested markets (token ids decoded by clean_event).
    assert page.rows[0]["markets"][0]["clobTokenIds"] == ["1"]
    await svc.aclose()


# --- holders clamp is announced ---


async def test_holders_clamp_sets_truncated(monkeypatch):
    svc = _service(monkeypatch, [{"token": "t", "holders": [
        {"pseudonym": "A", "amount": 1.0, "outcomeIndex": 0},
    ]}])
    page = await svc.holders("0xabc", limit=500)
    assert svc._last_params["limit"] == 100  # clamped before the upstream call
    assert page.truncated is True
    unclamped = await svc.holders("0xabc", limit=5)
    assert unclamped.truncated is False
    await svc.aclose()


# --- collect_markets wall-clock deadline ---


async def test_collect_markets_deadline_fails_loud(monkeypatch):
    from polygate_connector.core.errors import ValidationError
    from polygate_connector.services import facade as facade_module

    # Clock jumps far ahead on every read, so the deadline expires before the
    # first scan page completes.
    clock = {"now": 0.0}

    def fake_monotonic():
        clock["now"] += 1000.0
        return clock["now"]

    monkeypatch.setattr(facade_module, "monotonic", fake_monotonic)
    svc = _service(monkeypatch, [{"id": "e1", "markets": []}])
    with pytest.raises(ValidationError, match="time budget"):
        await svc.collect_markets(tag_id=1)
    await svc.aclose()
