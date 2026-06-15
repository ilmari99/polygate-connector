"""Gamma API client - market & event discovery (public, no auth).

Docs: https://docs.polymarket.com/market-data/fetching-markets
"""

from __future__ import annotations

from typing import Any

from .http import HttpClient


class GammaService:
    """Read-only access to Polymarket's Gamma discovery API."""

    def __init__(self, http: HttpClient, host: str):
        self._http = http
        self._host = host.rstrip("/")

    async def list_markets(self, **params: Any) -> Any:
        """List markets. Common params: active, closed, tag_id, limit, offset, order."""
        return await self._http.get_json(
            f"{self._host}/markets", params=_clean(params), source="gamma"
        )

    async def get_market(self, condition_id: str) -> Any:
        """Fetch a single market by its condition id."""
        return await self._http.get_json(
            f"{self._host}/markets", params={"condition_ids": condition_id}, source="gamma"
        )

    async def get_market_by_slug(self, slug: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/markets", params={"slug": slug}, source="gamma"
        )

    async def list_events(self, **params: Any) -> Any:
        """List events (each event groups one or more markets)."""
        return await self._http.get_json(
            f"{self._host}/events", params=_clean(params), source="gamma"
        )

    async def list_tags(self) -> Any:
        return await self._http.get_json(f"{self._host}/tags", source="gamma")

    async def search(self, query: str, **params: Any) -> Any:
        """Full-text search across events and markets (public-search).

        Returns ``{events: [...], pagination: {...}}``. Useful params: limit_per_type,
        events_status (e.g. 'active'), page.
        """
        return await self._http.get_json(
            f"{self._host}/public-search", params=_clean({"q": query, **params}), source="gamma"
        )

    async def list_comments(
        self, *, parent_entity_id: int, parent_entity_type: str = "Event", **params: Any
    ) -> Any:
        """Public comments on an event (or series). Returns a list of comments."""
        return await self._http.get_json(
            f"{self._host}/comments",
            params=_clean(
                {
                    "parent_entity_type": parent_entity_type,
                    "parent_entity_id": parent_entity_id,
                    **params,
                }
            ),
            source="gamma",
        )


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so they are not serialised into the query string."""
    return {k: v for k, v in params.items() if v is not None}
