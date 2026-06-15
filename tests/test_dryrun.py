"""Tests for the dry-run safety switch in the service facade.

These assert that the trading SDK is never constructed while in dry-run mode:
writes must be simulated.
"""

from __future__ import annotations

import pytest

from polygate.config import get_settings
from polygate.models.order import (
    OrderType,
    PlaceOrderRequest,
    Side,
)
from polygate.services.facade import PolymarketService


@pytest.fixture
def service() -> PolymarketService:
    get_settings.cache_clear()
    return PolymarketService(get_settings())


async def test_place_order_simulated_in_dry_run(service: PolymarketService):
    req = PlaceOrderRequest(token_id="123", side=Side.BUY, size=5, price=0.5)
    result = await service.place_order(req)
    assert result.simulated is True
    assert result.success is True
    assert result.status == "SIMULATED"
    assert result.order_id is None
    # Trading client must NOT have been constructed.
    assert service._trading is None  # noqa: SLF001
    await service.aclose()


async def test_cancel_simulated_in_dry_run(service: PolymarketService):
    result = await service.cancel_order("order-xyz")
    assert result.simulated is True
    assert result.canceled == ["order-xyz"]
    assert service._trading is None  # noqa: SLF001
    await service.aclose()


async def test_fok_without_price_simulated_without_signing(service: PolymarketService):
    # Even an FOK without price must not reach the SDK in dry-run.
    req = PlaceOrderRequest(token_id="123", side=Side.BUY, size=5, order_type=OrderType.FOK)
    result = await service.place_order(req)
    assert result.simulated is True
    assert service._trading is None  # noqa: SLF001
    await service.aclose()
