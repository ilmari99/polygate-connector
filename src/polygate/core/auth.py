"""API-key authentication for the platform's own REST surface.

The platform can move real money, so every request must carry the
``X-API-Key`` header matching ``PLATFORM_API_KEY``. Comparison is constant-time.
If no ``PLATFORM_API_KEY`` is configured the platform refuses to start (see
``main``), so this dependency can assume one is set.
"""

from __future__ import annotations

import secrets

from fastapi import Header

from ..config import Settings, get_settings
from .errors import AuthError


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """FastAPI dependency that validates the ``X-API-Key`` header."""
    settings: Settings = get_settings()
    expected = settings.platform_api_key
    if expected is None:
        # Defensive: startup should have prevented this.
        raise AuthError("Platform API key is not configured.")
    provided = x_api_key or ""
    if not secrets.compare_digest(provided, expected.get_secret_value()):
        raise AuthError("Missing or invalid X-API-Key.")
