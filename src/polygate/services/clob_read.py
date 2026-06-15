"""CLOB read-only endpoints — order book, prices, spread (public, no auth).

Docs: https://docs.polymarket.com/trading/orderbook
"""

from __future__ import annotations

from typing import Any

from .http import HttpClient


class ClobReadService:
    """Read-only access to the CLOB market endpoints (no credentials needed)."""

    def __init__(self, http: HttpClient, host: str):
        self._http = http
        self._host = host.rstrip("/")

    async def order_book(self, token_id: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/book", params={"token_id": token_id}, source="clob"
        )

    async def price(self, token_id: str, side: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/price",
            params={"token_id": token_id, "side": side.lower()},
            source="clob",
        )

    async def midpoint(self, token_id: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/midpoint", params={"token_id": token_id}, source="clob"
        )

    async def spread(self, token_id: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/spread", params={"token_id": token_id}, source="clob"
        )

    async def last_trade_price(self, token_id: str) -> Any:
        return await self._http.get_json(
            f"{self._host}/last-trade-price", params={"token_id": token_id}, source="clob"
        )

    async def prices_history(
        self,
        token_id: str,
        *,
        interval: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> Any:
        """Historical price series for a token.

        ``market`` is the CLOB token id here (Polymarket's parameter name).
        Provide either ``interval`` (e.g. '1h','1d','1w','max') or a
        ``start_ts``/``end_ts`` window.
        """
        params = {
            "market": token_id,
            "interval": interval,
            "startTs": start_ts,
            "endTs": end_ts,
            "fidelity": fidelity,
        }
        params = {k: v for k, v in params.items() if v is not None}
        return await self._http.get_json(
            f"{self._host}/prices-history", params=params, source="clob"
        )
