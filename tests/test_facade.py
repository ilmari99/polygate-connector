"""Tests for the shared facade operations layer.

The facade is the single source of truth both transports (REST + MCP) delegate
to, so the logic that used to be duplicated in each adapter - notably the search
``markets`` flattening - is verified here once.
"""

from __future__ import annotations

from polygate.services.facade import _flatten_search


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
