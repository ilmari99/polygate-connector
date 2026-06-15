"""Request/response models for trading actions."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, model_validator


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """Time-in-force / order kind.

    - GTC: good-til-cancelled limit order.
    - GTD: good-til-date (requires ``expiration``).
    - FOK: fill-or-kill (market order, fully filled or cancelled).
    - FAK: fill-and-kill (market order, fill what's possible, cancel the rest).
    """

    GTC = "GTC"
    GTD = "GTD"
    FOK = "FOK"
    FAK = "FAK"


class PlaceOrderRequest(BaseModel):
    token_id: str = Field(description="CLOB token id (the Yes or No outcome token).")
    side: Side
    size: float = Field(gt=0, description="Number of outcome shares.")
    price: float | None = Field(
        default=None, gt=0, lt=1, description="Limit price in (0,1). Required for GTC/GTD."
    )
    order_type: OrderType = OrderType.GTC
    expiration: int | None = Field(
        default=None, description="Unix seconds; required for GTD orders."
    )
    tick_size: str | None = Field(
        default=None, description="Market tick size, e.g. '0.01'. Auto-detected if omitted."
    )
    neg_risk: bool | None = Field(
        default=None, description="Whether this is a neg-risk market. Auto-detected if omitted."
    )

    @model_validator(mode="after")
    def _check_price_and_expiration(self) -> "PlaceOrderRequest":
        if self.order_type in (OrderType.GTC, OrderType.GTD) and self.price is None:
            raise ValueError(f"price is required for {self.order_type.value} orders")
        if self.order_type is OrderType.GTD and self.expiration is None:
            raise ValueError("expiration (unix seconds) is required for GTD orders")
        return self


class OrderResult(BaseModel):
    """Outcome of a place/cancel action (real or simulated)."""

    simulated: bool = Field(description="True when produced by dry-run, not sent upstream.")
    success: bool
    order_id: str | None = None
    status: str | None = None
    request: dict | None = None
    raw: dict | None = Field(default=None, description="Raw upstream response, when available.")


class CancelResult(BaseModel):
    simulated: bool
    success: bool
    canceled: list[str] = Field(default_factory=list)
    not_canceled: dict | None = None
    raw: dict | None = None
