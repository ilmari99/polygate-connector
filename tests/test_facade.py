"""Tests for the shared facade operations layer.

The facade is the single source of truth both transports (REST + MCP) delegate
to, so the logic that used to be duplicated in each adapter - notably the search
``markets`` flattening - is verified here once.
"""

from __future__ import annotations

import pytest

from polygate.config import get_settings
from polygate.core.errors import NotFoundError
from polygate.services.facade import PolymarketService, _flatten_search


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


class _FakeTrading:
    def __init__(self, *, trades=None, balance=None):
        self._trades = trades or []
        self._balance = balance or {}

    async def trades(self):
        return self._trades

    async def balance_allowance(self, *, conditional_token_id=None):
        return self._balance


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
        {"conditionId": "0xabc", "clobTokenIds": "[\"1\",\"2\"]", "description": "noise"}
    ])
    env = await svc.get_market("0xabc")
    assert isinstance(env.data, dict)  # NOT a 1-element list
    assert env.data["conditionId"] == "0xabc"
    assert env.data["clobTokenIds"] == ["1", "2"]  # decoded
    assert "description" not in env.data  # compact by default
    await svc.aclose()


async def test_get_market_not_found_raises(monkeypatch):
    svc = _service(monkeypatch, [])
    with pytest.raises(NotFoundError):
        await svc.get_market("0xdeadbeef")
    await svc.aclose()


async def test_get_market_full_when_compact_false(monkeypatch):
    svc = _service(monkeypatch, [{"conditionId": "0xabc", "description": "keep"}])
    env = await svc.get_market("0xabc", compact=False)
    assert env.data["description"] == "keep"
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


async def test_prices_history_respects_explicit_interval(monkeypatch):
    svc = _service(monkeypatch, {"history": []})
    env = await svc.prices_history("token1", interval="1d")
    assert svc._last_params["interval"] == "1d"
    assert env.data["interval"] == "1d"
    await svc.aclose()


# --- search flatten is optional (Category 3) ---


def _search_payload(_params):
    return {
        "events": [
            {"id": "7", "title": "T", "slug": "s",
             "markets": [{"id": "m1", "clobTokenIds": "[\"1\"]"}]}
        ],
        "pagination": {},
    }


async def test_search_no_flat_markets_by_default(monkeypatch):
    svc = _service(monkeypatch, _search_payload)
    env = await svc.search("q")
    assert "markets" not in env.data  # flat array is opt-in
    # nested markets still carry the decoded token ids to trade on
    assert env.data["events"][0]["markets"][0]["clobTokenIds"] == ["1"]
    await svc.aclose()


async def test_search_flatten_true_adds_tagged_markets(monkeypatch):
    svc = _service(monkeypatch, _search_payload)
    env = await svc.search("q", flatten=True)
    assert env.data["markets"][0]["event_id"] == "7"
    assert env.data["markets"][0]["clobTokenIds"] == ["1"]
    await svc.aclose()


# --- trades: bounded + compact (Category 3) ---


def _big_trade(i: int) -> dict:
    return {
        "id": f"t{i}", "market": "0xc", "asset_id": "1", "side": "BUY", "size": 1,
        "price": 0.5, "outcome": "Yes", "status": "OK", "match_time": "1",
        "fee_rate_bps": "0", "trader_side": "TAKER",
        "maker_orders": [{"order_id": "o"}], "transaction_hash": "0xh",
        "owner": "u", "bucket_index": 1, "last_update": "2",
    }


async def test_trades_limits_and_compacts(monkeypatch):
    svc = PolymarketService(get_settings())
    svc._trading = _FakeTrading(trades=[_big_trade(i) for i in range(5)])
    env = await svc.trades(limit=2)
    assert len(env.data) == 2  # bounded
    assert "maker_orders" not in env.data[0]  # compacted
    assert env.data[0]["price"] == 0.5
    await svc.aclose()


async def test_trades_full_when_compact_false(monkeypatch):
    svc = PolymarketService(get_settings())
    svc._trading = _FakeTrading(trades=[_big_trade(0)])
    env = await svc.trades(compact=False)
    assert "maker_orders" in env.data[0]
    await svc.aclose()


# --- balance allowance collapsing (issue #9) ---


async def test_balance_collapses_max_uint_allowances(monkeypatch):
    svc = PolymarketService(get_settings())
    svc._trading = _FakeTrading(balance={
        "balance": "100", "allowances": {"0xA": str(2**256 - 1), "0xB": "0"}
    })
    env = await svc.balance()
    assert env.data["allowances"]["0xA"] == "unlimited"
    assert env.data["allowances"]["0xB"] == "0"
    await svc.aclose()


# --- positions / activity compact (Category 3) ---


async def test_positions_compact_drops_icon(monkeypatch):
    svc = _service(monkeypatch, [{"conditionId": "0x1", "cashPnl": -1.0, "icon": "u"}])
    env = await svc.positions()
    assert "icon" not in env.data[0]
    assert env.data[0]["cashPnl"] == -1.0
    await svc.aclose()


async def test_activity_compact_drops_identity(monkeypatch):
    svc = _service(monkeypatch, [{"type": "TRADE", "price": 0.5, "name": "me", "bio": ""}])
    env = await svc.activity()
    assert "name" not in env.data[0] and "bio" not in env.data[0]
    assert env.data[0]["price"] == 0.5
    await svc.aclose()
