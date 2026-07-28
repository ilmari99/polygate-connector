"""Payload shaping for Polymarket's public API responses.

Gamma returns a few market fields (``outcomes``, ``outcomePrices``,
``clobTokenIds``) as JSON-encoded *strings* and ships every object with a large
tail of low-signal fields (descriptions, images, AMM internals, timestamps).
These helpers shape the payloads to a chosen verbosity tier:

* ``minimal`` - the handful of fields needed to scan a list and pick an id.
* ``compact`` - the high-signal projection an agent reasons about in detail.
* ``full`` - the raw upstream object (JSON-string fields still decoded).

``minimal`` and ``compact`` are also pruned: ``None``/empty-string values are
dropped and floats rounded to 4 decimal places. ``full`` is never pruned.
"""

from __future__ import annotations

import json
from typing import Any, Literal

Verbosity = Literal["minimal", "compact", "full"]

# Market fields Gamma encodes as JSON strings; decoded in place to real values.
_MARKET_JSON_FIELDS = ("outcomes", "outcomePrices", "clobTokenIds")

# Fields needed to scan a market list and pick an id to drill into.
_MINIMAL_MARKET_FIELDS = frozenset(
    {
        "question",
        "conditionId",
        "slug",
        "groupItemTitle",
        "outcomes",
        "outcomePrices",
        "active",
        "closed",
        "endDate",
        "liquidityNum",
        "volume24hr",
        # Added by search flattening so callers keep parent context.
        "event_slug",
    }
)

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
        # Added by search flattening so callers keep parent context.
        "event_id",
        "event_title",
        "event_slug",
    }
)

# Fields needed to scan an event list row.
_MINIMAL_EVENT_FIELDS = frozenset(
    {
        "id",
        "title",
        "slug",
        "active",
        "closed",
        "endDate",
        "liquidity",
        "volume",
        "markets",
        "seriesSlug",
        "gameId",
    }
)

# High-signal event fields kept in compact mode. ``markets`` is always kept and
# its entries are projected recursively. The grouping keys (``series``,
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

_MINIMAL_SERIES_FIELDS = frozenset(
    {"id", "slug", "title", "recurrence", "event_count", "volume24hr", "active"}
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

_MINIMAL_TAG_FIELDS = frozenset({"id", "label", "slug"})
_COMPACT_TAG_FIELDS = frozenset({"id", "label", "slug", "forceShow"})

_MINIMAL_COMMENT_FIELDS = frozenset({"body", "createdAt", "author", "reactionCount"})
_COMPACT_COMMENT_FIELDS = frozenset(
    {"id", "body", "createdAt", "author", "reactionCount", "reportCount", "parentCommentID"}
)

_MINIMAL_HOLDER_FIELDS = frozenset({"outcomeIndex", "name", "pseudonym", "amount"})
_COMPACT_HOLDER_FIELDS = frozenset(
    {"outcomeIndex", "name", "pseudonym", "amount", "proxyWallet", "asset"}
)

# How many nested markets an event list row keeps (ranked by liquidity).
TOP_MARKETS_PER_EVENT = 5

# How many price levels per side an order book summary keeps.
TOP_BOOK_LEVELS = 5


def prune(obj: Any) -> Any:
    """Drop ``None``/empty-string values and round floats to 4dp, recursively."""
    if isinstance(obj, dict):
        return {k: prune(v) for k, v in obj.items() if v is not None and v != ""}
    if isinstance(obj, list):
        return [prune(v) for v in obj]
    if isinstance(obj, float):
        return round(obj, 4)
    return obj


def _project(
    obj: dict[str, Any], verbosity: Verbosity, minimal: frozenset, compact: frozenset
) -> dict[str, Any]:
    """Apply the verbosity tier's field allowlist and pruning to one object."""
    if verbosity == "minimal":
        return prune({k: v for k, v in obj.items() if k in minimal})
    if verbosity == "compact":
        return prune({k: v for k, v in obj.items() if k in compact})
    return obj


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


def clean_market(market: Any, *, verbosity: Verbosity = "full") -> Any:
    """Decode a market's JSON fields and project it to the verbosity tier."""
    if not isinstance(market, dict):
        return market
    out = _decode_json_fields(dict(market))
    return _project(out, verbosity, _MINIMAL_MARKET_FIELDS, _COMPACT_MARKET_FIELDS)


def clean_event(event: Any, *, verbosity: Verbosity = "full") -> Any:
    """Clean an event and every market nested under it."""
    if not isinstance(event, dict):
        return event
    out = dict(event)
    markets = out.get("markets")
    if isinstance(markets, list):
        out["markets"] = [clean_market(m, verbosity=verbosity) for m in markets]
    return _project(out, verbosity, _MINIMAL_EVENT_FIELDS, _COMPACT_EVENT_FIELDS)


def _liquidity(market: Any) -> float:
    try:
        return float(market.get("liquidityNum") or 0.0)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def clean_event_for_list(event: Any, *, verbosity: Verbosity = "minimal") -> Any:
    """Project an event into a list row: nested markets become a count plus the
    top few by liquidity, so one event listing cannot inline hundreds of markets.

    ``full`` verbosity keeps the event untouched.
    """
    if not isinstance(event, dict) or verbosity == "full":
        return clean_event(event, verbosity=verbosity)
    out = clean_event(event, verbosity=verbosity)
    markets = out.get("markets")
    if not isinstance(markets, list):
        return out
    ranked = sorted(
        (m for m in markets if isinstance(m, dict)), key=_liquidity, reverse=True
    )
    out.pop("markets", None)
    out["market_count"] = len(markets)
    out["top_markets"] = ranked[:TOP_MARKETS_PER_EVENT]
    if len(markets) > TOP_MARKETS_PER_EVENT:
        key = out.get("slug") or out.get("id")
        out["note"] = (
            f"top {TOP_MARKETS_PER_EVENT} of {len(markets)} markets by liquidity; "
            f"get_event({key!r}) returns all of them"
        )
    return out


def clean_markets(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Clean a Gamma ``/markets`` response (a JSON array of market dicts)."""
    if isinstance(data, list):
        return [clean_market(m, verbosity=verbosity) for m in data]
    return data


def clean_events(data: Any, *, verbosity: Verbosity = "full", for_list: bool = False) -> Any:
    """Clean a Gamma ``/events`` response (a JSON array of event dicts)."""
    if isinstance(data, list):
        cleaner = clean_event_for_list if for_list else clean_event
        return [cleaner(e, verbosity=verbosity) for e in data]
    return data


def clean_series(series: Any, *, verbosity: Verbosity = "full") -> Any:
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
    return _project(out, verbosity, _MINIMAL_SERIES_FIELDS, _COMPACT_SERIES_FIELDS)


def clean_series_list(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Clean a Gamma ``/series`` response (a JSON array of series dicts)."""
    if isinstance(data, list):
        return [clean_series(s, verbosity=verbosity) for s in data]
    return data


def clean_tags(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Project a Gamma ``/tags`` response to the id/label/slug a caller filters by."""
    if not isinstance(data, list):
        return data
    return [
        _project(t, verbosity, _MINIMAL_TAG_FIELDS, _COMPACT_TAG_FIELDS)
        if isinstance(t, dict)
        else t
        for t in data
    ]


def clean_comments(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Project comments: the nested profile collapses to an ``author`` name."""
    if not isinstance(data, list):
        return data
    out = []
    for comment in data:
        if not isinstance(comment, dict):
            out.append(comment)
            continue
        row = dict(comment)
        profile = row.get("profile")
        if isinstance(profile, dict):
            row["author"] = profile.get("name") or profile.get("pseudonym")
        out.append(
            _project(row, verbosity, _MINIMAL_COMMENT_FIELDS, _COMPACT_COMMENT_FIELDS)
        )
    return out


def flatten_holders(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Flatten the Data-API ``/holders`` shape into one row per holder.

    Upstream nests holders under one entry per outcome token
    (``[{token, holders: [...]}]``); each flat row keeps its ``outcomeIndex``
    so the side is still identifiable without repeating the 78-digit token id.
    """
    if not isinstance(data, list) or verbosity == "full":
        return data
    rows: list[Any] = []
    for entry in data:
        if not isinstance(entry, dict) or not isinstance(entry.get("holders"), list):
            rows.append(entry)
            continue
        for holder in entry["holders"]:
            if not isinstance(holder, dict):
                continue
            row = {**holder, "asset": entry.get("token")}
            rows.append(
                _project(row, verbosity, _MINIMAL_HOLDER_FIELDS, _COMPACT_HOLDER_FIELDS)
            )
    return rows


def summarize_order_book(book: Any, *, full: bool = False) -> Any:
    """Summarize a CLOB book: best bid/ask, top levels with cumulative depth.

    The CLOB ``/book`` response ships raw ``bids``/``asks`` arrays that are not
    guaranteed sorted, so a caller otherwise has to scan them to find the best
    bid (max price) and best ask (min price). We compute a ``summary`` plus the
    top :data:`TOP_BOOK_LEVELS` levels per side (best-first, with cumulative
    size) and per-side depth totals. The full ladder is included only when
    ``full`` is set. Prices are coerced to floats; empty or missing sides yield
    ``None`` summary fields.
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

    def _size(value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    def _top(levels: list[tuple[float, Any]], *, best_first_desc: bool) -> list[dict[str, Any]]:
        ordered = sorted(levels, key=lambda x: x[0], reverse=best_first_desc)
        rows: list[dict[str, Any]] = []
        cumulative = 0.0
        for price, size in ordered[:TOP_BOOK_LEVELS]:
            cumulative += _size(size)
            rows.append(
                {"price": price, "size": size, "cumulative_size": round(cumulative, 4)}
            )
        return rows

    bids = _levels(book.get("bids"))
    asks = _levels(book.get("asks"))
    best_bid = max(bids, key=lambda x: x[0]) if bids else (None, None)
    best_ask = min(asks, key=lambda x: x[0]) if asks else (None, None)
    bid_p, ask_p = best_bid[0], best_ask[0]
    both = bid_p is not None and ask_p is not None
    out = dict(book) if full else {k: v for k, v in book.items() if k not in ("bids", "asks")}
    out["summary"] = {
        "best_bid": bid_p,
        "best_bid_size": best_bid[1],
        "best_ask": ask_p,
        "best_ask_size": best_ask[1],
        "midpoint": round((bid_p + ask_p) / 2, 4) if both else None,
        "spread": round(ask_p - bid_p, 4) if both else None,
    }
    out["bids_top"] = _top(bids, best_first_desc=True)
    out["asks_top"] = _top(asks, best_first_desc=False)
    out["depth"] = {
        "bid_levels": len(bids),
        "ask_levels": len(asks),
        "bid_shares": round(sum(_size(s) for _, s in bids), 4),
        "ask_shares": round(sum(_size(s) for _, s in asks), 4),
    }
    return out


def summarize_price_history(data: Any) -> Any:
    """Collapse a ``/prices-history`` series to its window summary.

    The raw response is ``{"history": [{"t": ts, "p": price}, ...]}`` with up
    to thousands of points; the summary keeps the window bounds, first/last/
    min/max prices, the change over the window, and the point count.
    """
    if not isinstance(data, dict) or not isinstance(data.get("history"), list):
        return data
    points: list[tuple[Any, float]] = []
    for pt in data["history"]:
        if not isinstance(pt, dict):
            continue
        try:
            points.append((pt.get("t"), float(pt.get("p"))))
        except (TypeError, ValueError):
            continue
    out = {k: v for k, v in data.items() if k != "history"}
    if not points:
        out["summary"] = {"n_points": 0}
        return out
    prices = [p for _, p in points]
    out["summary"] = {
        "start_ts": points[0][0],
        "end_ts": points[-1][0],
        "first": round(prices[0], 4),
        "last": round(prices[-1], 4),
        "min": round(min(prices), 4),
        "max": round(max(prices), 4),
        "change": round(prices[-1] - prices[0], 4),
        "n_points": len(points),
    }
    return out


def clean_search_events(data: Any, *, verbosity: Verbosity = "full") -> Any:
    """Project search's ``events`` into list rows (see :func:`clean_event_for_list`)."""
    if not isinstance(data, list):
        return data
    return [clean_event_for_list(e, verbosity=verbosity) for e in data]
