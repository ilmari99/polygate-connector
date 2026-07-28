"""Payload cleanups for Gamma market/event responses.

Gamma returns a few market fields (``outcomes``, ``outcomePrices``,
``clobTokenIds``) as JSON-encoded *strings* and ships every object with a large
tail of low-signal fields (descriptions, images, AMM internals, timestamps).
These helpers make the payloads easier for an agent to consume:

* :func:`clean_market` / :func:`clean_event` decode the embedded JSON arrays so
  callers never double-parse, and - when ``compact`` is set - keep only the
  high-signal fields an agent actually reasons about.
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
        "event_slug",
    }
)

# High-signal event fields kept in compact mode. ``markets`` is always kept and
# its entries are compacted recursively. The grouping keys (``series``,
# ``seriesSlug``, ``gameId``, ``tags``, ``negRiskMarketID``) are kept too: they
# are how a caller navigates to the sibling events Polymarket splits a topic
# across, so dropping them would hide the discovery path.
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
        "series",
        "seriesSlug",
        "gameId",
        "tags",
        "negRiskMarketID",
    }
)

# High-signal series fields kept in compact mode.
_COMPACT_SERIES_FIELDS = frozenset(
    {
        "id",
        "ticker",
        "slug",
        "title",
        "seriesType",
        "recurrence",
        "active",
        "closed",
        "startDate",
        "volume24hr",
        "event_count",
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


def clean_series(series: Any, *, compact: bool = False) -> Any:
    """Clean a series object for the catalog listing.

    A series embeds its full ``events`` list, which is heavy; it is replaced with
    an ``event_count`` so the listing stays a lightweight index. Drill into a
    series' events with ``list_events(series_id=...)``.
    """
    if not isinstance(series, dict):
        return series
    out = dict(series)
    events = out.get("events")
    if isinstance(events, list):
        out["event_count"] = len(events)
    out.pop("events", None)
    if compact:
        out = {k: v for k, v in out.items() if k in _COMPACT_SERIES_FIELDS}
    return out


def clean_series_list(data: Any, *, compact: bool = False) -> Any:
    """Clean a Gamma ``/series`` response (a JSON array of series dicts)."""
    if isinstance(data, list):
        return [clean_series(s, compact=compact) for s in data]
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


def summarize_order_book(book: Any) -> Any:
    """Attach a derived ``summary`` (best bid/ask, midpoint, spread) to a book.

    The CLOB ``/book`` response ships raw ``bids``/``asks`` arrays that are not
    guaranteed sorted, so a caller otherwise has to scan them to find the best
    bid (max price) and best ask (min price). We compute those once here - plus
    the midpoint and spread - so the agent reads them directly instead of parsing
    the ladder (and needing a separate price/spread/midpoint tool). Prices are
    coerced to floats; ``size`` at the top of book is carried through as-is. Empty
    or missing sides yield ``None`` fields.
    """
    if not isinstance(book, dict):
        return book

    def _levels(side: Any) -> list[tuple[float, Any]]:
        out: list[tuple[float, Any]] = []
        for lvl in side or []:
            if not isinstance(lvl, dict):
                continue
            try:
                out.append((float(lvl.get("price")), lvl.get("size")))
            except (TypeError, ValueError):
                continue
        return out

    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    best_bid = max(bids, key=lambda x: x[0]) if bids else (None, None)
    best_ask = min(asks, key=lambda x: x[0]) if asks else (None, None)
    bid_p, ask_p = best_bid[0], best_ask[0]
    both = bid_p is not None and ask_p is not None
    out = dict(book)
    out["summary"] = {
        "best_bid": bid_p,
        "best_bid_size": best_bid[1],
        "best_ask": ask_p,
        "best_ask_size": best_ask[1],
        "midpoint": round((bid_p + ask_p) / 2, 6) if both else None,
        "spread": round(ask_p - bid_p, 6) if both else None,
    }
    return out
