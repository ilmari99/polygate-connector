"""Tests for Gamma payload cleanup: JSON decoding and compact projection."""

from __future__ import annotations

from polygate.services.transform import (
    clean_activity,
    clean_balance,
    clean_event,
    clean_events,
    clean_market,
    clean_markets,
    clean_positions,
    clean_search,
    clean_series,
    clean_series_list,
    clean_trade,
    clean_trades,
)


def _raw_market() -> dict:
    return {
        "id": "m1",
        "question": "Will it?",
        "conditionId": "0xabc",
        "clobTokenIds": '["111", "222"]',
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.6", "0.4"]',
        "description": "a very long description" * 20,
        "image": "https://example.com/i.png",
        "icon": "https://example.com/ic.png",
        "volumeNum": 1234.5,
        "endDate": "2026-07-31T00:00:00Z",
    }


def test_clean_market_decodes_json_string_fields():
    out = clean_market(_raw_market())
    assert out["clobTokenIds"] == ["111", "222"]
    assert out["outcomes"] == ["Yes", "No"]
    assert out["outcomePrices"] == ["0.6", "0.4"]
    # Full (non-compact) view keeps every field, just decoded.
    assert "description" in out and "image" in out


def test_clean_market_compact_drops_noise_keeps_signal():
    out = clean_market(_raw_market(), compact=True)
    assert out["clobTokenIds"] == ["111", "222"]  # still decoded
    assert out["conditionId"] == "0xabc"
    assert out["volumeNum"] == 1234.5
    assert out["endDate"] == "2026-07-31T00:00:00Z"
    # Low-signal fields are stripped.
    for noise in ("description", "image", "icon"):
        assert noise not in out


def test_clean_market_leaves_bad_json_untouched():
    out = clean_market({"clobTokenIds": "not-json"})
    assert out["clobTokenIds"] == "not-json"


def test_clean_market_passthrough_non_dict():
    assert clean_market("nope") == "nope"
    assert clean_markets([1, 2]) == [1, 2]


def test_clean_event_cleans_nested_markets():
    event = {
        "id": "e1",
        "title": "Rain?",
        "description": "noise",
        "markets": [_raw_market()],
    }
    out = clean_event(event, compact=True)
    assert out["id"] == "e1"
    assert "description" not in out
    assert out["markets"][0]["clobTokenIds"] == ["111", "222"]
    assert "description" not in out["markets"][0]


def test_clean_events_list():
    out = clean_events([{"id": "e1", "markets": [_raw_market()]}])
    assert out[0]["markets"][0]["outcomes"] == ["Yes", "No"]


def test_clean_search_cleans_events_and_flat_markets():
    data = {
        "events": [{"id": "e1", "markets": [_raw_market()]}],
        "markets": [_raw_market()],
    }
    out = clean_search(data, compact=True)
    assert out["markets"][0]["clobTokenIds"] == ["111", "222"]
    assert "description" not in out["markets"][0]
    assert out["events"][0]["markets"][0]["outcomes"] == ["Yes", "No"]


def test_clean_event_compact_keeps_grouping_keys():
    # Compact must retain the keys used to navigate to sibling events, while
    # still dropping genuine noise.
    event = {
        "id": "e1",
        "title": "Brazil vs. Norway",
        "description": "noise",
        "image": "https://example.com/i.png",
        "series": [{"id": "11433"}],
        "seriesSlug": "soccer-fifwc",
        "gameId": 90086997,
        "tags": [{"id": "1", "slug": "sports"}],
        "negRiskMarketID": "0xabc",
        "markets": [_raw_market()],
    }
    out = clean_event(event, compact=True)
    assert out["series"] == [{"id": "11433"}]
    assert out["seriesSlug"] == "soccer-fifwc"
    assert out["gameId"] == 90086997
    assert out["tags"] == [{"id": "1", "slug": "sports"}]
    assert out["negRiskMarketID"] == "0xabc"
    assert "description" not in out and "image" not in out


def test_clean_series_list_drops_events_for_count():
    raw = [{"id": "35", "slug": "fomc", "title": "FOMC",
            "description": "noise", "events": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}]
    out = clean_series_list(raw)
    assert out[0]["event_count"] == 3
    assert "events" not in out[0]
    # Full (non-compact) view keeps other metadata.
    assert out[0]["title"] == "FOMC"


def test_clean_series_compact_projects_allowlist():
    raw = {"id": "35", "slug": "fomc", "title": "FOMC", "recurrence": "monthly",
           "description": "noise", "events": [{"id": "a"}]}
    out = clean_series(raw, compact=True)
    assert out["event_count"] == 1
    assert out["recurrence"] == "monthly"
    assert "description" not in out


# --- Account tools: trades, positions, activity, balance ---


def _raw_trade() -> dict:
    return {
        "id": "t1",
        "market": "0xcond",
        "asset_id": "111",
        "side": "BUY",
        "size": 20,
        "price": 0.51,
        "outcome": "Yes",
        "status": "CONFIRMED",
        "match_time": "1783286926",
        "fee_rate_bps": "0",
        "trader_side": "TAKER",
        # noise:
        "maker_orders": [{"order_id": "o1", "matched_amount": "20"}],
        "transaction_hash": "0xhash",
        "owner": "uuid-owner",
        "maker_address": "0xmaker",
        "taker_order_id": "0xtaker",
        "bucket_index": 3,
        "last_update": "1783286927",
    }


def test_clean_trade_compact_keeps_signal_drops_plumbing():
    out = clean_trade(_raw_trade(), compact=True)
    assert out["id"] == "t1"
    assert out["price"] == 0.51
    assert out["trader_side"] == "TAKER"
    for noise in ("maker_orders", "transaction_hash", "owner", "maker_address",
                  "taker_order_id", "bucket_index", "last_update"):
        assert noise not in out


def test_clean_trade_non_compact_is_untouched():
    raw = _raw_trade()
    assert clean_trade(raw, compact=False) == raw
    assert clean_trades([raw]) == [raw]  # default compact=False


def test_clean_trades_compacts_each():
    out = clean_trades([_raw_trade(), _raw_trade()], compact=True)
    assert len(out) == 2
    assert all("maker_orders" not in t for t in out)


def test_clean_positions_drops_icon_only_when_compact():
    raw = [{"conditionId": "0x1", "size": 2500, "cashPnl": -26.25,
            "icon": "https://x/i.png"}]
    assert clean_positions(raw, compact=False) == raw
    out = clean_positions(raw, compact=True)
    assert "icon" not in out[0]
    assert out[0]["cashPnl"] == -26.25  # signal preserved


def test_clean_activity_drops_identity_noise_when_compact():
    raw = [{
        "conditionId": "0x1", "type": "TRADE", "size": 20, "price": 0.51,
        "icon": "https://x/i.png", "name": "ilmari99", "pseudonym": "Sneaky-Citrus",
        "bio": "", "profileImage": "", "profileImageOptimized": "",
    }]
    assert clean_activity(raw, compact=False) == raw
    out = clean_activity(raw, compact=True)
    for noise in ("icon", "name", "pseudonym", "bio", "profileImage", "profileImageOptimized"):
        assert noise not in out[0]
    assert out[0]["price"] == 0.51  # signal preserved


def test_clean_balance_collapses_max_uint_allowances():
    max_uint = str(2**256 - 1)
    data = {"balance": "1875491510",
            "allowances": {"0xA": max_uint, "0xB": "0", "0xC": "12345"}}
    out = clean_balance(data)
    assert out["balance"] == "1875491510"  # untouched
    assert out["allowances"]["0xA"] == "unlimited"
    assert out["allowances"]["0xB"] == "0"       # finite value kept (real signal)
    assert out["allowances"]["0xC"] == "12345"


def test_clean_balance_passthrough_without_allowances():
    assert clean_balance({"balance": "10"}) == {"balance": "10"}
    assert clean_balance("nope") == "nope"
