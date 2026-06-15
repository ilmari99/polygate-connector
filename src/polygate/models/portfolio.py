"""Lightweight pass-through models for market & portfolio data.

Polymarket's upstream payloads are large and evolve over time, so the platform
forwards them as flexible dictionaries inside a :class:`ResponseEnvelope` rather
than pinning a rigid schema that could drift. These models exist mainly for
OpenAPI documentation of the common fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class BalanceInfo(BaseModel):
    asset_type: str
    balance: str | None = None
    raw: dict[str, Any] | None = None


# Market, order-book, position, and value payloads are forwarded verbatim as
# ``dict``/``list`` inside ResponseEnvelope[...] to stay resilient to upstream
# schema changes. Aliases kept for documentation/readability at call sites.
MarketPayload = dict[str, Any]
OrderBookPayload = dict[str, Any]
PositionPayload = dict[str, Any]
