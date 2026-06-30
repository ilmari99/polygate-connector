"""Tests for Gamma payload cleanup: JSON decoding and compact projection."""

from __future__ import annotations

from polygate.services.transform import (
    clean_event,
    clean_events,
    clean_market,
    clean_markets,
    clean_search,
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
