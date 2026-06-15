"""System endpoints: health and (non-secret) configuration."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from ..config import get_settings
from ..core.auth import require_api_key
from .deps import get_service

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    """Liveness probe and current run mode (no auth required)."""
    settings = get_settings()
    return {
        "status": "ok",
        "mode": "dry-run" if settings.dry_run else "live",
        "wallet_address": settings.funder_address,
    }


@router.get("/config", dependencies=[Depends(require_api_key)])
async def config(service=Depends(get_service)) -> dict:
    """Return the active, secret-free configuration summary."""
    return get_settings().public_summary()
