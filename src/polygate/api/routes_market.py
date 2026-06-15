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
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order: str | None = None,
    ascending: bool | None = None,
    service=Depends(get_service),
) -> ResponseEnvelope:
    if slug:
        data = await service.gamma.get_market_by_slug(slug)
    else:
        data = await service.gamma.list_markets(
            active=active,
            closed=closed,
            tag_id=tag_id,
            limit=limit,
            offset=offset,
            order=order,
            ascending=ascending,
        )
    return ResponseEnvelope.of(data, source="gamma")


@router.get("/{condition_id}")
async def get_market(condition_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.gamma.get_market(condition_id)
    return ResponseEnvelope.of(data, source="gamma")


# --- Events / tags ---
events_router = APIRouter(tags=["market-data"])


@events_router.get("/events")
async def list_events(
    active: bool | None = Query(default=True),
    closed: bool | None = Query(default=False),
    tag_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    order: str | None = None,
    service=Depends(get_service),
) -> ResponseEnvelope:
    data = await service.gamma.list_events(
        active=active, closed=closed, tag_id=tag_id, limit=limit, offset=offset, order=order
    )
    return ResponseEnvelope.of(data, source="gamma")


@events_router.get("/tags")
async def list_tags(service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.gamma.list_tags()
    return ResponseEnvelope.of(data, source="gamma")


# --- CLOB order book / prices (keyed by token id) ---
@book_router.get("/orderbook/{token_id}")
async def order_book(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.clob.order_book(token_id)
    return ResponseEnvelope.of(data, source="clob")


@book_router.get("/price/{token_id}")
async def price(
    token_id: str,
    side: str = Query(default="BUY", pattern="^(?i)(BUY|SELL)$"),
    service=Depends(get_service),
) -> ResponseEnvelope:
    data = await service.clob.price(token_id, side)
    return ResponseEnvelope.of(data, source="clob")


@book_router.get("/midpoint/{token_id}")
async def midpoint(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.clob.midpoint(token_id)
    return ResponseEnvelope.of(data, source="clob")


@book_router.get("/spread/{token_id}")
async def spread(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.clob.spread(token_id)
    return ResponseEnvelope.of(data, source="clob")


@book_router.get("/last-trade-price/{token_id}")
async def last_trade_price(token_id: str, service=Depends(get_service)) -> ResponseEnvelope:
    data = await service.clob.last_trade_price(token_id)
    return ResponseEnvelope.of(data, source="clob")


@book_router.get("/prices-history/{token_id}")
async def prices_history(
    token_id: str,
    interval: str | None = Query(default=None, description="e.g. 1h, 6h, 1d, 1w, max"),
    start_ts: int | None = None,
    end_ts: int | None = None,
    fidelity: int | None = Query(default=None, description="Resolution in minutes."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    data = await service.clob.prices_history(
        token_id, interval=interval, start_ts=start_ts, end_ts=end_ts, fidelity=fidelity
    )
    return ResponseEnvelope.of(data, source="clob")


# --- Research: search, comments, holders (so agents never call raw upstreams) ---
research_router = APIRouter(tags=["research"])


@research_router.get("/search")
async def search(
    q: str = Query(..., min_length=1, description="Free-text query over events and markets."),
    limit_per_type: int | None = Query(default=None, ge=1, le=100),
    page: int | None = Query(default=None, ge=1),
    events_status: str | None = Query(default=None, description="e.g. 'active', 'resolved'."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Full-text search across Polymarket events and markets (Gamma)."""
    data = await service.gamma.search(
        q, limit_per_type=limit_per_type, page=page, events_status=events_status
    )
    return ResponseEnvelope.of(data, source="gamma")


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
    data = await service.gamma.list_comments(
        parent_entity_id=event_id, limit=limit, offset=offset, order=order, ascending=ascending
    )
    return ResponseEnvelope.of(data, source="gamma")


@research_router.get("/holders/{condition_id}")
async def holders(
    condition_id: str,
    limit: int = Query(default=100, ge=1, le=500),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Top holders for a market, grouped by outcome token (Data API)."""
    data = await service.data.holders(condition_id, limit=limit)
    return ResponseEnvelope.of(data, source="data")
