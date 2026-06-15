"""Tests for response envelope and order request validation."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from polygate.models.common import ResponseEnvelope
from polygate.models.order import OrderType, PlaceOrderRequest, Side


def test_envelope_sets_timestamp_and_source():
    env = ResponseEnvelope.of({"hello": "world"}, source="gamma")
    assert env.data == {"hello": "world"}
    assert env.source == "gamma"
    assert env.fetched_at.tzinfo is not None
    # Timestamp should be very recent and timezone-aware (UTC).
    delta = datetime.now(timezone.utc) - env.fetched_at
    assert 0 <= delta.total_seconds() < 5


def test_limit_order_requires_price():
    with pytest.raises(ValidationError):
        PlaceOrderRequest(token_id="t", side=Side.BUY, size=10, order_type=OrderType.GTC)


def test_gtd_order_requires_expiration():
    with pytest.raises(ValidationError):
        PlaceOrderRequest(
            token_id="t", side=Side.BUY, size=10, price=0.5, order_type=OrderType.GTD
        )


def test_valid_limit_order():
    req = PlaceOrderRequest(token_id="t", side=Side.BUY, size=10, price=0.42)
    assert req.order_type is OrderType.GTC
    assert req.price == 0.42


def test_price_bounds_enforced():
    with pytest.raises(ValidationError):
        PlaceOrderRequest(token_id="t", side=Side.BUY, size=10, price=1.5)
    with pytest.raises(ValidationError):
        PlaceOrderRequest(token_id="t", side=Side.SELL, size=10, price=0)
