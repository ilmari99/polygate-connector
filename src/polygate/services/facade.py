"""The central service facade used by the API routes.

It owns the upstream clients and enforces the dry-run safety switch: in dry-run
mode every state-changing action (place/cancel order) is simulated and
audit-logged but never signed or broadcast. Read operations always execute for
real because they are free and side-effect-free.
"""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..constants import GAMMA_PAGE_LIMIT
from ..core.errors import ConfigurationError
from ..core.logging import audit
from ..models.common import ResponseEnvelope
from ..models.order import CancelResult, OrderResult, PlaceOrderRequest
from .http import HttpClient
from .trading import TradingService
from .transform import clean_events, clean_markets, clean_search


class PolymarketService:
    """Aggregates all upstream access behind one dry-run-aware interface."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = HttpClient(
            timeout=settings.http_timeout_seconds, max_retries=settings.http_max_retries
        )
        self._gamma_host = settings.gamma_host.rstrip("/")
        self._clob_host = settings.clob_host.rstrip("/")
        self._data_host = settings.data_host.rstrip("/")
        self._trading: TradingService | None = None

    async def aclose(self) -> None:
        await self._http.aclose()

    @property
    def dry_run(self) -> bool:
        return self._settings.dry_run

    def trading(self) -> TradingService:
        """Lazily build the authenticated CLOB client."""
        if self._trading is None:
            self._trading = TradingService.from_settings(self._settings)
        return self._trading

    def require_funder(self) -> str:
        if not self._settings.funder_address:
            raise ConfigurationError(
                "FUNDER_ADDRESS not configured; cannot query account data."
            )
        return self._settings.funder_address

    async def _read(
        self, host: str, path: str, source: str, params: dict[str, Any] | None = None
    ) -> Any:
        """GET ``host + path`` (dropping None params) and tag the upstream ``source``."""
        clean = {k: v for k, v in (params or {}).items() if v is not None}
        return await self._http.get_json(f"{host}{path}", params=clean, source=source)

    async def _read_paged(
        self,
        host: str,
        path: str,
        source: str,
        params: dict[str, Any],
        *,
        limit: int,
        offset: int,
    ) -> Any:
        """Read a Gamma list endpoint, paging past its 100-row server cap.

        Gamma silently truncates any single page to ``GAMMA_PAGE_LIMIT`` rows, so
        a request for more than that is fanned out into consecutive offset pages
        and concatenated. Stops early when a short page signals the upstream is
        exhausted. A non-list response (an error shape) is returned untouched.
        """
        if limit <= GAMMA_PAGE_LIMIT:
            return await self._read(
                host, path, source, {**params, "limit": limit, "offset": offset}
            )
        collected: list[Any] = []
        cursor = offset
        while len(collected) < limit:
            page_size = min(GAMMA_PAGE_LIMIT, limit - len(collected))
            page = await self._read(
                host, path, source, {**params, "limit": page_size, "offset": cursor}
            )
            if not isinstance(page, list):
                return page if not collected else collected
            collected.extend(page)
            if len(page) < page_size:
                break  # Upstream returned a short page: no more rows available.
            cursor += page_size
        return collected[:limit]

    # --- Market data (Gamma) ---
    async def list_markets(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        tag_id: int | None = None,
        slug: str | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
        compact: bool = False,
    ) -> ResponseEnvelope:
        """List markets, or fetch a single market by ``slug`` when given."""
        if slug:
            data = await self._read(self._gamma_host, "/markets", "gamma", {"slug": slug})
        else:
            data = await self._read_paged(
                self._gamma_host,
                "/markets",
                "gamma",
                {
                    "active": active,
                    "closed": closed,
                    "tag_id": tag_id,
                    "order": order,
                    "ascending": ascending,
                },
                limit=limit,
                offset=offset,
            )
        return ResponseEnvelope.of(clean_markets(data, compact=compact), source="gamma")

    async def get_market(self, condition_id: str, *, compact: bool = False) -> ResponseEnvelope:
        data = await self._read(
            self._gamma_host, "/markets", "gamma", {"condition_ids": condition_id}
        )
        return ResponseEnvelope.of(clean_markets(data, compact=compact), source="gamma")

    async def list_events(
        self,
        *,
        active: bool | None = True,
        closed: bool | None = False,
        tag_id: int | None = None,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        compact: bool = False,
    ) -> ResponseEnvelope:
        data = await self._read_paged(
            self._gamma_host,
            "/events",
            "gamma",
            {
                "active": active,
                "closed": closed,
                "tag_id": tag_id,
                "order": order,
            },
            limit=limit,
            offset=offset,
        )
        return ResponseEnvelope.of(clean_events(data, compact=compact), source="gamma")

    async def list_tags(self) -> ResponseEnvelope:
        data = await self._read(self._gamma_host, "/tags", "gamma")
        return ResponseEnvelope.of(data, source="gamma")

    # --- CLOB book / prices (keyed by outcome token id) ---
    async def order_book(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(self._clob_host, "/book", "clob", {"token_id": token_id})
        return ResponseEnvelope.of(data, source="clob")

    async def price(self, token_id: str, side: str = "BUY") -> ResponseEnvelope:
        data = await self._read(
            self._clob_host, "/price", "clob", {"token_id": token_id, "side": side.lower()}
        )
        return ResponseEnvelope.of(data, source="clob")

    async def midpoint(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(self._clob_host, "/midpoint", "clob", {"token_id": token_id})
        return ResponseEnvelope.of(data, source="clob")

    async def spread(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(self._clob_host, "/spread", "clob", {"token_id": token_id})
        return ResponseEnvelope.of(data, source="clob")

    async def last_trade_price(self, token_id: str) -> ResponseEnvelope:
        data = await self._read(
            self._clob_host, "/last-trade-price", "clob", {"token_id": token_id}
        )
        return ResponseEnvelope.of(data, source="clob")

    async def prices_history(
        self,
        token_id: str,
        *,
        interval: str | None = None,
        start_ts: int | None = None,
        end_ts: int | None = None,
        fidelity: int | None = None,
    ) -> ResponseEnvelope:
        data = await self._read(
            self._clob_host,
            "/prices-history",
            "clob",
            {
                "market": token_id,
                "interval": interval,
                "startTs": start_ts,
                "endTs": end_ts,
                "fidelity": fidelity,
            },
        )
        return ResponseEnvelope.of(data, source="clob")

    # --- Research ---
    async def search(
        self,
        q: str,
        *,
        limit_per_type: int | None = None,
        page: int | None = None,
        events_status: str | None = None,
        compact: bool = False,
    ) -> ResponseEnvelope:
        """Full-text search; also surfaces a flat ``markets`` list (see ``_flatten_search``)."""
        data = await self._read(
            self._gamma_host,
            "/public-search",
            "gamma",
            {
                "q": q,
                "limit_per_type": limit_per_type,
                "page": page,
                "events_status": events_status,
            },
        )
        cleaned = clean_search(_flatten_search(data), compact=compact)
        return ResponseEnvelope.of(cleaned, source="gamma")

    async def comments(
        self,
        event_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        order: str | None = None,
        ascending: bool | None = None,
    ) -> ResponseEnvelope:
        data = await self._read(
            self._gamma_host,
            "/comments",
            "gamma",
            {
                "parent_entity_type": "Event",
                "parent_entity_id": event_id,
                "limit": limit,
                "offset": offset,
                "order": order,
                "ascending": ascending,
            },
        )
        return ResponseEnvelope.of(data, source="gamma")

    async def holders(self, condition_id: str, *, limit: int = 100) -> ResponseEnvelope:
        data = await self._read(
            self._data_host, "/holders", "data", {"market": condition_id, "limit": limit}
        )
        return ResponseEnvelope.of(data, source="data")

    # --- Portfolio / account (require a configured wallet) ---
    async def positions(self, *, limit: int = 100) -> ResponseEnvelope:
        data = await self._read(
            self._data_host, "/positions", "data", {"user": self.require_funder(), "limit": limit}
        )
        return ResponseEnvelope.of(data, source="data")

    async def portfolio_value(self) -> ResponseEnvelope:
        data = await self._read(
            self._data_host, "/value", "data", {"user": self.require_funder()}
        )
        return ResponseEnvelope.of(data, source="data")

    async def balance(self, *, token_id: str | None = None) -> ResponseEnvelope:
        data = await self.trading().balance_allowance(conditional_token_id=token_id)
        return ResponseEnvelope.of(data, source="clob")

    async def activity(self, *, limit: int = 100) -> ResponseEnvelope:
        data = await self._read(
            self._data_host, "/activity", "data", {"user": self.require_funder(), "limit": limit}
        )
        return ResponseEnvelope.of(data, source="data")

    async def open_orders(
        self, *, market: str | None = None, asset_id: str | None = None
    ) -> ResponseEnvelope:
        data = await self.trading().open_orders(market=market, asset_id=asset_id)
        return ResponseEnvelope.of(data, source="clob")

    async def trades(self) -> ResponseEnvelope:
        data = await self.trading().trades()
        return ResponseEnvelope.of(data, source="clob")

    # --- Actions (dry-run aware) ---
    async def place_order(self, req: PlaceOrderRequest) -> OrderResult:
        fields = {
            "token_id": req.token_id,
            "side": req.side.value,
            "size": req.size,
            "price": req.price,
            "order_type": req.order_type.value,
        }
        if self.dry_run:
            audit("place_order", dry_run=True, **fields)
            return OrderResult(
                simulated=True,
                success=True,
                status="SIMULATED",
                request=fields,
            )
        raw = await self.trading().place_order(req)
        audit("place_order", dry_run=False, **fields, response=raw)
        return OrderResult(
            simulated=False,
            success=bool(raw.get("success", True)),
            order_id=raw.get("orderID"),
            status=raw.get("status"),
            request=fields,
            raw=raw,
        )

    async def cancel_order(self, order_id: str) -> CancelResult:
        if self.dry_run:
            audit("cancel_order", dry_run=True, order_id=order_id)
            return CancelResult(simulated=True, success=True, canceled=[order_id])
        raw = await self.trading().cancel_order(order_id)
        audit("cancel_order", dry_run=False, order_id=order_id, response=raw)
        return CancelResult(
            simulated=False,
            success=True,
            canceled=raw.get("canceled") or [order_id],
            not_canceled=raw.get("not_canceled"),
            raw=raw,
        )

    async def cancel_all(self) -> CancelResult:
        if self.dry_run:
            audit("cancel_all", dry_run=True)
            return CancelResult(simulated=True, success=True, canceled=[])
        raw = await self.trading().cancel_all()
        audit("cancel_all", dry_run=False, response=raw)
        return CancelResult(
            simulated=False,
            success=True,
            canceled=raw.get("canceled") or [],
            not_canceled=raw.get("not_canceled"),
            raw=raw,
        )


def _flatten_search(data: Any) -> Any:
    """Surface a flat ``markets`` list on Gamma search results.

    Gamma groups markets under events, so the outcome token ids live at
    ``events[].markets[].clobTokenIds``. We add a top-level ``markets`` array
    (each entry tagged with its parent ``event_id``/``event_title``) so a caller
    can read ``clobTokenIds`` directly without drilling into every event. A
    response that already carries ``markets`` is returned untouched.
    """
    if not isinstance(data, dict) or "markets" in data:
        return data
    flat: list[dict] = []
    for event in data.get("events") or []:
        if not isinstance(event, dict):
            continue
        event_id = event.get("id")
        event_title = event.get("title")
        for market in event.get("markets") or []:
            if isinstance(market, dict):
                flat.append({**market, "event_id": event_id, "event_title": event_title})
    return {**data, "markets": flat}
