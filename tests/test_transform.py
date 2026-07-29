"""Tests for payload shaping: JSON decoding, verbosity tiers, and summaries."""

from __future__ import annotations

from polygate_connector.services.transform import (
    clean_comments,
    clean_event,
    clean_event_for_list,
    clean_events,
    clean_market,
    clean_markets,
    clean_series,
    clean_series_list,
    clean_tags,
    flatten_holders,
    prune,
    summarize_order_book,
    summarize_price_history,
)


def _raw_market(**extra) -> dict:
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
        "liquidityNum": 500.123456,
        "endDate": "2026-07-31T00:00:00Z",
        **extra,
    }


# --- prune ---


def test_prune_drops_nulls_and_empty_strings_recursively():
    out = prune({"a": None, "b": "", "c": {"d": None, "e": 1}, "f": [None, {"g": ""}]})
    assert out == {"c": {"e": 1}, "f": [None, {}]}


def test_prune_rounds_floats_to_4dp():
    out = prune({"x": 0.123456789, "nested": [1.00005]})
    assert out["x"] == 0.1235
    assert out["nested"] == [round(1.00005, 4)]
    assert prune(True) is True  # bools are not floats


# --- markets ---


def test_clean_market_full_decodes_json_string_fields():
    out = clean_market(_raw_market())
    assert out["clobTokenIds"] == ["111", "222"]
    assert out["outcomes"] == ["Yes", "No"]
    assert out["outcomePrices"] == ["0.6", "0.4"]
    # Full view keeps every field, just decoded.
    assert "description" in out and "image" in out


def test_clean_market_compact_drops_noise_keeps_signal():
    out = clean_market(_raw_market(), verbosity="compact")
    assert out["clobTokenIds"] == ["111", "222"]  # still decoded
    assert out["conditionId"] == "0xabc"
    assert out["volumeNum"] == 1234.5
    assert out["endDate"] == "2026-07-31T00:00:00Z"
    for noise in ("description", "image", "icon"):
        assert noise not in out


def test_clean_market_minimal_keeps_only_scan_fields():
    out = clean_market(_raw_market(), verbosity="minimal")
    assert out["conditionId"] == "0xabc"
    assert out["question"] == "Will it?"
    assert out["outcomePrices"] == ["0.6", "0.4"]
    assert out["liquidityNum"] == 500.1235  # pruned: rounded to 4dp
    # clobTokenIds are a get_market concern, not a list-scan one.
    assert "clobTokenIds" not in out
    assert "id" not in out


def test_clean_market_minimal_keeps_book_edge():
    # outcomePrices is a midpoint; rows must carry the executable edge too.
    out = clean_market(
        _raw_market(bestBid=0.01, bestAsk=0.65, spread=0.64), verbosity="minimal"
    )
    assert out["bestBid"] == 0.01
    assert out["bestAsk"] == 0.65
    assert out["spread"] == 0.64


def test_clean_market_compact_keeps_resolution_and_fee_signals():
    out = clean_market(
        _raw_market(
            umaResolutionStatus="disputed",
            umaResolutionStatuses='["proposed", "disputed"]',
            feesEnabled=True,
            feeType="weather_fees",
            feeSchedule={"rate": 0.05, "takerOnly": True},
        ),
        verbosity="compact",
    )
    assert out["umaResolutionStatus"] == "disputed"
    assert out["umaResolutionStatuses"] == ["proposed", "disputed"]  # decoded
    assert out["feesEnabled"] is True
    assert out["feeType"] == "weather_fees"
    assert out["feeSchedule"] == {"rate": 0.05, "takerOnly": True}


def test_clean_market_strips_stale_context_in_embedded_events():
    market = _raw_market(
        events=[{"id": "e1", "eventMetadata": {
            "context_description": "stale narrative",
            "context_requires_regen": True,
        }}]
    )
    out = clean_market(market)  # full: embedded events only survive here
    meta = out["events"][0]["eventMetadata"]
    assert "context_description" not in meta
    assert meta["context_requires_regen"] is True


def test_clean_market_minimal_prunes_nulls():
    out = clean_market(_raw_market(volume24hr=None), verbosity="minimal")
    assert "volume24hr" not in out


def test_clean_market_leaves_bad_json_untouched():
    out = clean_market({"clobTokenIds": "not-json"})
    assert out["clobTokenIds"] == "not-json"


def test_clean_market_passthrough_non_dict():
    assert clean_market("nope") == "nope"
    assert clean_markets([1, 2]) == [1, 2]


# --- events ---


def test_clean_event_cleans_nested_markets():
    event = {
        "id": "e1",
        "title": "Rain?",
        "description": "noise",
        "markets": [_raw_market()],
    }
    out = clean_event(event, verbosity="compact")
    assert out["id"] == "e1"
    assert "description" not in out
    assert out["markets"][0]["clobTokenIds"] == ["111", "222"]
    assert "description" not in out["markets"][0]


def test_clean_event_compact_keeps_grouping_keys():
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
    out = clean_event(event, verbosity="compact")
    assert out["series"] == [{"id": "11433"}]
    assert out["seriesSlug"] == "soccer-fifwc"
    assert out["gameId"] == 90086997
    assert out["tags"] == [{"id": "1", "slug": "sports"}]
    assert out["negRiskMarketID"] == "0xabc"
    assert "description" not in out and "image" not in out


def test_clean_event_compact_projects_nested_tags_and_series():
    event = {
        "id": "e1",
        "title": "Big",
        "tags": [{
            "id": "78", "label": "Iran", "slug": "iran", "forceShow": False,
            "publishedAt": "2023-11-02", "updatedBy": 15, "createdAt": "x",
            "updatedAt": "y", "requiresTranslation": False,
        }],
        "series": [{
            "id": "3", "slug": "geo", "title": "Geo", "recurrence": "weekly",
            "commentsEnabled": True, "createdAt": "x", "competitive": "0.9",
        }],
        "markets": [],
    }
    out = clean_event(event, verbosity="compact")
    assert out["tags"] == [
        {"id": "78", "label": "Iran", "slug": "iran", "forceShow": False}
    ]
    assert out["series"] == [
        {"id": "3", "slug": "geo", "title": "Geo", "recurrence": "weekly"}
    ]
    # Full stays raw.
    full = clean_event(event, verbosity="full")
    assert "updatedBy" in full["tags"][0]


def test_clean_event_strips_stale_context_at_every_tier():
    event = {
        "id": "e1",
        "title": "T",
        "eventMetadata": {
            "context_description": "stale narrative",
            "context_requires_regen": True,
            "context_updated_at": "2026-06-18",
        },
    }
    full = clean_event(event, verbosity="full")
    assert "context_description" not in full["eventMetadata"]
    assert full["eventMetadata"]["context_updated_at"] == "2026-06-18"
    # The input object is not mutated.
    assert "context_description" in event["eventMetadata"]


def test_clean_event_keeps_fresh_context_at_full():
    event = {
        "id": "e1",
        "eventMetadata": {"context_description": "current", "context_requires_regen": False},
    }
    out = clean_event(event, verbosity="full")
    assert out["eventMetadata"]["context_description"] == "current"


def test_clean_event_neg_risk_annotation():
    event = {
        "id": "e1",
        "negRisk": True,
        "markets": [
            _raw_market(outcomePrices='["0.62", "0.38"]', groupItemTitle="Alice", active=True),
            _raw_market(outcomePrices='["0.33", "0.67"]', groupItemTitle="Bob", active=True),
            _raw_market(outcomePrices='["0.20", "0.80"]', groupItemTitle="Other", active=True),
            # Closed markets don't count toward the sum.
            _raw_market(outcomePrices='["0.99", "0.01"]', groupItemTitle="Carol", closed=True),
        ],
    }
    out = clean_event(event, verbosity="compact")
    assert out["outcome_price_sum"] == round(0.62 + 0.33 + 0.20, 4)
    assert out["has_active_other"] is True
    # Survives the list-row projection (markets -> top_markets).
    row = clean_event_for_list(event, verbosity="minimal")
    assert row["outcome_price_sum"] == round(0.62 + 0.33 + 0.20, 4)


def test_clean_event_non_neg_risk_gets_no_annotation():
    event = {"id": "e1", "markets": [_raw_market()]}
    out = clean_event(event, verbosity="compact")
    assert "outcome_price_sum" not in out
    # Full tier is never annotated, negRisk or not.
    full = clean_event({**event, "negRisk": True}, verbosity="full")
    assert "outcome_price_sum" not in full


def test_clean_event_for_list_keeps_top_markets_by_liquidity():
    markets = [_raw_market(id=f"m{i}", liquidityNum=float(i)) for i in range(8)]
    event = {"id": "e1", "title": "Big", "slug": "big", "markets": markets}
    out = clean_event_for_list(event, verbosity="minimal")
    assert out["market_count"] == 8
    assert len(out["top_markets"]) == 5
    # Ranked by liquidity, best first.
    assert out["top_markets"][0]["liquidityNum"] == 7.0
    assert "markets" not in out
    assert "get_event('big')" in out["note"]


def test_clean_event_for_list_small_event_has_no_note():
    event = {"id": "e1", "slug": "s", "markets": [_raw_market()]}
    out = clean_event_for_list(event, verbosity="minimal")
    assert out["market_count"] == 1
    assert len(out["top_markets"]) == 1
    assert "note" not in out


def test_clean_event_for_list_full_is_untouched():
    event = {"id": "e1", "markets": [_raw_market()], "description": "keep"}
    out = clean_event_for_list(event, verbosity="full")
    assert "markets" in out and "description" in out


def test_clean_events_for_list_applies_row_projection():
    out = clean_events(
        [{"id": "e1", "markets": [_raw_market()]}], verbosity="minimal", for_list=True
    )
    assert out[0]["market_count"] == 1


# --- series / tags ---


def test_clean_series_list_drops_events_for_count():
    raw = [{"id": "35", "slug": "fomc", "title": "FOMC",
            "description": "noise", "events": [{"id": "a"}, {"id": "b"}, {"id": "c"}]}]
    out = clean_series_list(raw)
    assert out[0]["event_count"] == 3
    assert "events" not in out[0]
    # Full view keeps other metadata.
    assert out[0]["title"] == "FOMC"


def test_clean_series_minimal_projects_allowlist():
    raw = {"id": "35", "slug": "fomc", "title": "FOMC", "recurrence": "monthly",
           "description": "noise", "events": [{"id": "a"}]}
    out = clean_series(raw, verbosity="minimal")
    assert out["event_count"] == 1
    assert out["recurrence"] == "monthly"
    assert "description" not in out


def test_clean_tags_minimal_keeps_id_label_slug_forceshow():
    raw = [{"id": "1", "label": "Politics", "slug": "politics", "forceShow": False,
            "createdAt": "2020-01-01", "requiresTranslation": False}]
    out = clean_tags(raw, verbosity="minimal")
    # forceShow rides along (False is data, not noise - prune only drops None/"").
    assert out == [{"id": "1", "label": "Politics", "slug": "politics", "forceShow": False}]


# --- comments / holders ---


def test_clean_comments_collapses_profile_to_author():
    raw = [{
        "id": 1, "body": "hi", "createdAt": "2026-01-01T00:00:00Z",
        "profile": {"name": "alice", "pseudonym": "Quick-Fox", "bio": "..."},
        "reactions": [{"id": "r1"}], "reactionCount": 3, "userAddress": "0xdead",
    }]
    out = clean_comments(raw, verbosity="minimal")
    assert out[0] == {
        "body": "hi", "createdAt": "2026-01-01T00:00:00Z",
        "author": "alice", "reactionCount": 3,
    }


def test_flatten_holders_one_row_per_holder():
    raw = [
        {"token": "111", "holders": [
            {"proxyWallet": "0xa", "name": "alice", "pseudonym": "A", "amount": 10.123456,
             "outcomeIndex": 0, "bio": "x", "profileImage": "http://x"},
        ]},
        {"token": "222", "holders": [
            {"proxyWallet": "0xb", "pseudonym": "B", "amount": 5.0, "outcomeIndex": 1},
        ]},
    ]
    out = flatten_holders(raw, verbosity="minimal")
    assert len(out) == 2
    assert out[0] == {"name": "alice", "pseudonym": "A", "amount": 10.1235, "outcomeIndex": 0}
    assert out[1]["outcomeIndex"] == 1
    # compact keeps the wallet and token asset id.
    compact = flatten_holders(raw, verbosity="compact")
    assert compact[0]["proxyWallet"] == "0xa"
    assert compact[0]["asset"] == "111"
    # full keeps the raw nested shape.
    assert flatten_holders(raw, verbosity="full") == raw


# --- order book ---


def _book() -> dict:
    return {
        "tick_size": "0.01",
        "bids": [{"price": "0.48", "size": "10"}, {"price": "0.50", "size": "7"},
                 {"price": "0.45", "size": "3"}, {"price": "0.40", "size": "2"},
                 {"price": "0.35", "size": "1"}, {"price": "0.30", "size": "9"}],
        "asks": [{"price": "0.55", "size": "9"}, {"price": "0.51", "size": "3"},
                 {"price": "0.60", "size": "4"}],
    }


def test_summarize_order_book_computes_best_and_spread():
    out = summarize_order_book(_book())
    s = out["summary"]
    assert s["best_bid"] == 0.50 and s["best_bid_size"] == "7"
    assert s["best_ask"] == 0.51 and s["best_ask_size"] == "3"
    assert s["midpoint"] == 0.505
    assert s["spread"] == 0.01


def test_summarize_order_book_top_levels_with_cumulative_depth():
    out = summarize_order_book(_book())
    # Top 5 of 6 bid levels, best (highest) first, with running size.
    assert [lvl["price"] for lvl in out["bids_top"]] == [0.50, 0.48, 0.45, 0.40, 0.35]
    assert out["bids_top"][1]["cumulative_size"] == 17.0
    # Asks best (lowest) first.
    assert [lvl["price"] for lvl in out["asks_top"]] == [0.51, 0.55, 0.60]
    assert out["depth"] == {
        "bid_levels": 6, "ask_levels": 3, "bid_shares": 32.0, "ask_shares": 16.0,
    }


def test_summarize_order_book_drops_ladder_unless_full():
    slim = summarize_order_book(_book())
    assert "bids" not in slim and "asks" not in slim
    assert slim["tick_size"] == "0.01"
    full = summarize_order_book(_book(), full=True)
    assert len(full["bids"]) == 6 and len(full["asks"]) == 3


def test_summarize_order_book_empty_sides_yield_none():
    out = summarize_order_book({"bids": [], "asks": []})
    assert out["summary"]["best_bid"] is None
    assert out["bids_top"] == [] and out["asks_top"] == []


# --- price history ---


def test_summarize_price_history_window_stats():
    data = {"history": [
        {"t": 100, "p": 0.50}, {"t": 200, "p": 0.40},
        {"t": 300, "p": 0.65}, {"t": 400, "p": 0.60},
    ]}
    out = summarize_price_history(data)
    assert "history" not in out
    assert out["summary"] == {
        "start_ts": 100, "end_ts": 400,
        "first": 0.5, "last": 0.6, "min": 0.4, "max": 0.65,
        "change": 0.1, "n_points": 4,
    }


def test_summarize_price_history_empty_series():
    out = summarize_price_history({"history": []})
    assert out["summary"] == {"n_points": 0}


def test_summarize_price_history_passthrough_odd_shapes():
    assert summarize_price_history("nope") == "nope"
    assert summarize_price_history({"other": 1}) == {"other": 1}
