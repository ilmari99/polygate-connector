"""Trading action endpoints (dry-run aware)."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..core.auth import require_api_key
from ..models.order import CancelResult, OrderResult, PlaceOrderRequest
from .deps import get_service

router = APIRouter(tags=["trading"], dependencies=[Depends(require_api_key)])


@router.post("/orders", response_model=OrderResult)
async def place_order(req: PlaceOrderRequest, service=Depends(get_service)) -> OrderResult:
    """Place an order on Polymarket."""
    return await service.place_order(req)


@router.post("/orders/cancel-all", response_model=CancelResult)
async def cancel_all(service=Depends(get_service)) -> CancelResult:
    return await service.cancel_all()


@router.post("/orders/{order_id}/cancel", response_model=CancelResult)
async def cancel_order(order_id: str, service=Depends(get_service)) -> CancelResult:
    return await service.cancel_order(order_id)
