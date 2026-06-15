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
    user = service.require_funder()
    data = await service.data.positions(user, limit=limit)
    return ResponseEnvelope.of(data, source="data")


@router.get("/portfolio/value")
async def value(service=Depends(get_service)) -> ResponseEnvelope:
    """Current portfolio value for the configured wallet (Data API)."""
    user = service.require_funder()
    data = await service.data.value(user)
    return ResponseEnvelope.of(data, source="data")


@router.get("/portfolio/balance")
async def balance(
    token_id: str | None = Query(default=None, description="Conditional token id; omit for collateral."),
    service=Depends(get_service),
) -> ResponseEnvelope:
    """Collateral (USDC) or conditional-token balance & allowance (authenticated CLOB)."""
    data = await service.trading().balance_allowance(conditional_token_id=token_id)
    return ResponseEnvelope.of(data, source="clob")


@router.get("/activity")
async def activity(
    limit: int = Query(default=100, ge=1, le=500),
    service=Depends(get_service),
) -> ResponseEnvelope:
    user = service.require_funder()
    data = await service.data.activity(user, limit=limit)
    return ResponseEnvelope.of(data, source="data")


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
    data = await service.trading().open_orders(market=market, asset_id=asset_id)
    return ResponseEnvelope.of(data, source="clob")


@router.get("/trades")
async def trades(service=Depends(get_service)) -> ResponseEnvelope:
    """Trade history for the configured wallet (authenticated CLOB)."""
    data = await service.trading().trades()
    return ResponseEnvelope.of(data, source="clob")
