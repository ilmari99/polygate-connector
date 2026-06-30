"""Payload cleanups for Gamma market/event responses.

Gamma returns a few market fields (``outcomes``, ``outcomePrices``,
``clobTokenIds``) as JSON-encoded *strings* and ships every object with a large
tail of low-signal fields (descriptions, images, AMM internals, timestamps).
These helpers make the payloads easier for an agent to consume:

* :func:`clean_market` / :func:`clean_event` decode the embedded JSON arrays so
  callers never double-parse, and - when ``compact`` is set - keep only the
  high-signal fields a trading agent actually reasons about.
* :func:`clean_markets`, :func:`clean_events`, and :func:`clean_search` apply
  that cleanup across the shapes the three Gamma list endpoints return.

Decoding always runs; compaction is opt-in so the full payload stays available.
"""

from __future__ import annotations

import json
from typing import Any

# Market fields Gamma encodes as JSON strings; decoded in place to real values.
_MARKET_JSON_FIELDS = ("outcomes", "outcomePrices", "clobTokenIds")

# High-signal market fields kept in compact mode; everything else is dropped as
# noise (descriptions, images, AMM internals, audit timestamps, duplicate isos).
_COMPACT_MARKET_FIELDS = frozenset(
    {
        "id",
        "question",
        "conditionId",
        "slug",
        "groupItemTitle",
        "outcomes",
        "outcomePrices",
        "clobTokenIds",
        "active",
        "closed",
        "archived",
        "restricted",
        "enableOrderBook",
        "startDate",
        "endDate",
        "liquidityNum",
        "volumeNum",
        "volume24hr",
        "bestBid",
        "bestAsk",
        "lastTradePrice",
        "spread",
        "orderPriceMinTickSize",
        "orderMinSize",
        "negRisk",
        "negativeRisk",
        # Added by search flattening so callers keep parent context.
        "event_id",
        "event_title",
    }
)

# High-signal event fields kept in compact mode. ``markets`` is always kept and
# its entries are compacted recursively.
_COMPACT_EVENT_FIELDS = frozenset(
    {
        "id",
        "title",
        "slug",
        "active",
        "closed",
        "archived",
        "startDate",
        "endDate",
        "liquidity",
        "volume",
        "markets",
    }
)


def _decode_json_fields(market: dict[str, Any]) -> dict[str, Any]:
    """Decode Gamma's JSON-string market fields in place, leaving bad values."""
    for key in _MARKET_JSON_FIELDS:
        value = market.get(key)
        if isinstance(value, str):
            try:
                market[key] = json.loads(value)
            except (ValueError, TypeError):
                pass  # Not valid JSON - leave the raw string untouched.
    return market


def clean_market(market: Any, *, compact: bool = False) -> Any:
    """Decode a market's JSON fields and optionally drop low-signal noise."""
    if not isinstance(market, dict):
        return market
    out = _decode_json_fields(dict(market))
    if compact:
        out = {k: v for k, v in out.items() if k in _COMPACT_MARKET_FIELDS}
    return out


def clean_event(event: Any, *, compact: bool = False) -> Any:
    """Clean an event and every market nested under it."""
    if not isinstance(event, dict):
        return event
    out = dict(event)
    markets = out.get("markets")
    if isinstance(markets, list):
        out["markets"] = [clean_market(m, compact=compact) for m in markets]
    if compact:
        out = {k: v for k, v in out.items() if k in _COMPACT_EVENT_FIELDS}
    return out


def clean_markets(data: Any, *, compact: bool = False) -> Any:
    """Clean a Gamma ``/markets`` response (a JSON array of market dicts)."""
    if isinstance(data, list):
        return [clean_market(m, compact=compact) for m in data]
    return data


def clean_events(data: Any, *, compact: bool = False) -> Any:
    """Clean a Gamma ``/events`` response (a JSON array of event dicts)."""
    if isinstance(data, list):
        return [clean_event(e, compact=compact) for e in data]
    return data


def clean_search(data: Any, *, compact: bool = False) -> Any:
    """Clean a flattened search payload (``events`` plus a flat ``markets``)."""
    if not isinstance(data, dict):
        return data
    out = dict(data)
    if isinstance(out.get("events"), list):
        out["events"] = [clean_event(e, compact=compact) for e in out["events"]]
    if isinstance(out.get("markets"), list):
        out["markets"] = [clean_market(m, compact=compact) for m in out["markets"]]
    return out
