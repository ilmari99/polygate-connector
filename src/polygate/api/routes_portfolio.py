"""Portfolio & account endpoints (positions, value, balances, orders, trades)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from ..core.auth import require_api_key
from ..models.common import ResponseEnvelope
from .deps import get_service

router = APIRouter(tags=["portfolio"], dependencies=[Depends(require_api_key)])


@router.get("/portfolio/positions")
async def positions(
    limit: int = Query(default=100, ge=1, le=500),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Open positions for the configured wallet (Data API)."""
    return await service.positions(limit=limit)


@router.get("/portfolio/value")
async def value(service=Depends(get_service)) -> ResponseEnvelope:
    """Current portfolio value for the configured wallet (Data API)."""
    return await service.portfolio_value()


@router.get("/portfolio/balance")
async def balance(
    token_id: str | None = Query(default=None, description="Conditional token id; omit for collateral."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Collateral (USDC) or conditional-token balance & allowance (authenticated CLOB)."""
    return await service.balance(token_id=token_id)


@router.get("/activity")
async def activity(
    limit: int = Query(default=100, ge=1, le=500),
    service=Depends(get_service),
) -> ResponseEnvelope:
    return await service.activity(limit=limit)


@router.get("/orders")
async def open_orders(
    market: str | None = Query(default=None, description="Filter by market (condition id)."),
    asset_id: str | None = Query(
        default=None,
        description=(
            "Filter by CLOB token id. The CLOB may return no orders for an "
            "unfiltered query, so pass a market or asset_id to list reliably."
        ),
    ),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Open orders for the configured wallet (authenticated CLOB)."""
    return await service.open_orders(market=market, asset_id=asset_id)


@router.get("/trades")
async def trades(service=Depends(get_service)) -> ResponseEnvelope:
    """Trade history for the configured wallet (authenticated CLOB)."""
    return await service.trades()
