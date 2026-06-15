"""The central service facade used by the API routes.

It owns the upstream clients and enforces the dry-run safety switch: in dry-run
mode every state-changing action (place/cancel order) is simulated and
audit-logged but never signed or broadcast. Read operations always execute for
real because they are free and side-effect-free.
"""

from __future__ import annotations

from ..config import Settings
from ..core.errors import ConfigurationError
from ..core.logging import audit
from ..models.order import CancelResult, OrderResult, PlaceOrderRequest
from .clob_read import ClobReadService
from .data_api import DataApiService
from .gamma import GammaService
from .http import HttpClient
from .trading import TradingService


class PolymarketService:
    """Aggregates all upstream access behind one dry-run-aware interface."""

    def __init__(self, settings: Settings):
        self._settings = settings
        self._http = HttpClient(
            timeout=settings.http_timeout_seconds, max_retries=settings.http_max_retries
        )
        self.gamma = GammaService(self._http, settings.gamma_host)
        self.clob = ClobReadService(self._http, settings.clob_host)
        self.data = DataApiService(self._http, settings.data_host)
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
