"""Market-data endpoints (public data, wrapped with a fetch timestamp)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..models.common import ResponseEnvelope
from .deps import get_service

# Market-data and research endpoints read only public Polymarket data, so they
# carry no platform-key dependency. Account endpoints (portfolio, trading) do.
router = APIRouter(prefix="/markets", tags=["market-data"])
book_router = APIRouter(tags=["market-data"])


@router.get("")
async def list_markets(
    active: bool | None = Query(default=True),
    closed: bool | None = Query(default=False),
    tag_id: int | None = None,
    slug: str | None = None,
    limit: int = Query(
        default=50,
        ge=1,
        le=1000,
        description="Rows to return. Values over 100 are paged across Gamma's 100-row cap.",
    ),
    offset: int = Query(default=0, ge=0),
    order: str | None = None,
    ascending: bool | None = None,
    compact: bool = Query(
        default=True,
        description="Drop low-signal fields (descriptions, images, AMM internals). Set false for full objects.",
    ),
    service=Depends(get_service),
) -> ResponseEnvelope:
    return await service.list_markets(
        active=active,
        closed=closed,
        tag_id=tag_id,
        slug=slug,
        limit=limit,
        offset=offset,
        order=order,
        ascending=ascending,
        compact=compact,
    )


@router.get("/{condition_id}")
async def get_market(
    condition_id: str,
    compact: bool = Query(default=True, description="Drop low-signal fields. Set false for the full object."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    return await service.get_market(condition_id, compact=compact)


# --- Events / tags ---
events_router = APIRouter(tags=["market-data"])


@events_router.get("/events")
async def list_events(
    active: bool | None = Query(default=True),
    closed: bool | None = Query(default=False),
    tag_id: int | None = None,
    series_id: int | None = Query(
        default=None,
        description="Narrow to one series (a recurring/multi-part group of events).",
    ),
    limit: int = Query(
        default=50,
        ge=1,
        le=1000,
        description="Rows to return. Values over 100 are paged across Gamma's 100-row cap.",
    ),
    offset: int = Query(default=0, ge=0),
    order: str | None = None,
    compact: bool = Query(
        default=True,
        description="Drop low-signal fields (descriptions, images, AMM internals). Set false for full objects.",
    ),
    service=Depends(get_service),
) -> ResponseEnvelope:
    return await service.list_events(
        active=active,
        closed=closed,
        tag_id=tag_id,
        series_id=series_id,
        limit=limit,
        offset=offset,
        order=order,
        compact=compact,
    )


@events_router.get("/events/{key}")
async def get_event(
    key: str,
    compact: bool = Query(default=True, description="Drop low-signal fields. Set false for the full object."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Fetch a single event (with its nested markets) by slug or event id."""
    return await service.get_event(key, compact=compact)


# --- Series (the recurring/multi-part grouping of events) ---
@events_router.get("/series")
async def list_series(
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
    compact: bool = Query(default=True, description="Drop low-signal fields. Set false for full objects."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Catalog of series; each carries an ``event_count`` (drill in with list_events?series_id=)."""
    return await service.list_series(limit=limit, offset=offset, compact=compact)


# --- Flatten: every atomic market under one grouping node ---
@events_router.get("/collect-markets")
async def collect_markets(
    series_id: int | None = Query(default=None, description="Flatten this series' markets."),
    tag_id: int | None = Query(default=None, description="Flatten this category's markets."),
    event: str | None = Query(
        default=None, description="Event slug or id; with group_by, expands to its group."
    ),
    group_by: str | None = Query(
        default=None,
        description="Event attribute (e.g. 'gameId') to narrow an event's series by.",
    ),
    active: bool | None = Query(default=True),
    closed: bool | None = Query(default=False),
    compact: bool = Query(default=True, description="Drop low-signal fields. Set false for full objects."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Flatten every atomic market under one scope (series, tag, or event) into a list.

    Pass exactly one of ``series_id``, ``tag_id``, or ``event``. Each returned
    market is tagged with its parent ``event_id``/``event_title``/``event_slug``
    and carries its ``conditionId`` and ``clobTokenIds``. This is how you gather
    markets Polymarket buries across sibling events (e.g. a sports fixture:
    ``event=<fixture-slug>&group_by=gameId``). The scan runs to completion and
    fails loud if the scope is too broad - never silently partial.
    """
    return await service.collect_markets(
        series_id=series_id,
        tag_id=tag_id,
        event=event,
        group_by=group_by,
        active=active,
        closed=closed,
        compact=compact,
    )


@events_router.get("/tags")
async def list_tags(service=Depends(get_service)) -> ResponseEnvelope:
    return await service.list_tags()


# --- CLOB order book / prices (keyed by token id) ---
@book_router.get("/orderbook/{token_id}")
async def order_book(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    return await service.order_book(token_id)


@book_router.get("/last-trade-price/{token_id}")
async def last_trade_price(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    return await service.last_trade_price(token_id)


@book_router.get("/prices-history/{token_id}")
async def prices_history(
    token_id: str,
    interval: str | None = Query(default=None, description="e.g. 1h, 6h, 1d, 1w, max"),
    start_ts: int | None = None,
    end_ts: int | None = None,
    fidelity: int | None = Query(default=None, description="Resolution in minutes."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    return await service.prices_history(
        token_id, interval=interval, start_ts=start_ts, end_ts=end_ts, fidelity=fidelity
    )


# --- Research: search, comments, holders (so agents never call raw upstreams) ---
research_router = APIRouter(tags=["research"])


@research_router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Free-text query over events and markets."),
    limit_per_type: int | None = Query(default=None, ge=1, le=100),
    page: int | None = Query(default=None, ge=1),
    events_status: str | None = Query(default=None, description="e.g. 'active', 'resolved'."),
    compact: bool = Query(
        default=True,
        description="Drop low-signal fields (descriptions, images, AMM internals). Set false for full objects.",
    ),
    flatten: bool = Query(
        default=False,
        description=(
            "Also return a flat top-level `markets` array tagged with parent "
            "event context. Off by default: it duplicates every nested market."
        ),
    ),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Full-text search across Polymarket events and markets (Gamma).

    Gamma groups markets under events, so token ids live at
    ``events[].markets[].clobTokenIds`` (decoded to arrays). Pass ``flatten=true``
    to also get a flat top-level ``markets`` array, each entry tagged with its
    parent ``event_id``/``event_title``/``event_slug``.
    """
    return await service.search(
        q,
        limit_per_type=limit_per_type,
        page=page,
        events_status=events_status,
        compact=compact,
        flatten=flatten,
    )


@research_router.get("/comments")
async def comments(
    event_id: int = Query(..., description="Numeric event id (markets group under an event)."),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order: str | None = None,
    ascending: bool | None = None,
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Public comments on an event (Gamma)."""
    return await service.comments(
        event_id, limit=limit, offset=offset, order=order, ascending=ascending
    )


@research_router.get("/holders/{condition_id}")
async def holders(
    condition_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Top holders for a market, grouped by outcome token (Data API)."""
    return await service.holders(condition_id, limit=limit)
