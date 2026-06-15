"""Data API client — positions, portfolio value, activity (public, by address).

Docs: https://docs.polymarket.com (Data API). All endpoints take a ``user``
address and require no authentication.
"""

from __future__ import annotations

from typing import Any

from .http import HttpClient


class DataApiService:
    """Read-only portfolio/activity data keyed by wallet address."""

    def __init__(self, http: HttpClient, host: str):
        self._http = http
        self._host = host.rstrip("/")

    async def positions(self, user: str, **params: Any) -> Any:
        q = {"user": user, **{k: v for k, v in params.items() if v is not None}}
        return await self._http.get_json(f"{self._host}/positions", params=q, source="data")

    async def value(self, user: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/value", params={"user": user}, source="data"
        )

    async def activity(self, user: str, **params: Any) -> Any:
        q = {"user": user, **{k: v for k, v in params.items() if v is not None}}
        return await self._http.get_json(f"{self._host}/activity", params=q, source="data")
