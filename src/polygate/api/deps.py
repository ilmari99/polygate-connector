"""Shared FastAPI dependencies."""

from __future__ import annotations

from fastapi import Request

from ..services.facade import PolymarketService


def get_service(request: Request) -> PolymarketService:
    """Return the process-wide :class:`PolymarketService` from app state."""
    return request.app.state.service
