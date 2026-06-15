"""Authenticated CLOB trading via the official ``py-clob-client-v2`` SDK.

The SDK is synchronous and performs network and signing work, so every call is
dispatched to a worker thread with :func:`asyncio.to_thread` to avoid blocking
the FastAPI event loop. The client is built lazily so the platform can run in
dry-run mode without credentials.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

from py_clob_client_v2.clob_types import (
    ApiCreds,
    AssetType,
    BalanceAllowanceParams,
    OpenOrderParams,
    OrderArgsV2,
    OrderPayload,
    PartialCreateOrderOptions,
)
from py_clob_client_v2.client import ClobClient

from ..config import Settings
from ..constants import CHAIN_ID, SIGNATURE_TYPE
from ..core.errors import ConfigurationError, UpstreamError
from ..models.order import OrderType, PlaceOrderRequest, Side


def _to_plain(obj: Any) -> Any:
    """Best-effort conversion of SDK dataclasses/objects to JSON-able structures."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {k: _to_plain(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, dict):
        return {k: _to_plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_to_plain(v) for v in obj]
    if hasattr(obj, "__dict__"):
        return {k: _to_plain(v) for k, v in vars(obj).items()}
    return str(obj)


def _upstream_message(prefix: str, exc: Exception) -> str:
    """Turn an SDK exception into a concise, user-facing reason.

    The CLOB SDK raises ``PolyApiException`` with a ``status_code`` and an
    ``error_msg`` that is usually ``{"error": "<reason>"}``. Surface just the
    reason (and the upstream status) instead of the raw exception repr.
    """
    status = getattr(exc, "status_code", None)
    raw = getattr(exc, "error_msg", None)
    if isinstance(raw, dict):
        detail = raw.get("error") or raw.get("message") or str(raw)
    elif raw:
        detail = str(raw)
    else:
        detail = str(exc)
    return f"{prefix}: {detail}" + (f" (upstream HTTP {status})" if status else "")


class TradingService:
    """Wraps the authenticated CLOB client. Construct via :meth:`from_settings`."""

    def __init__(self, client: ClobClient):
        self._client = client

    @classmethod
    def from_settings(cls, settings: Settings) -> "TradingService":
        """Build a credentialed client, or raise :class:`ConfigurationError`."""
        if not settings.has_wallet:
            raise ConfigurationError(
                "Wallet not configured. Set PRIVATE_KEY and FUNDER_ADDRESS in .env."
            )
        if not settings.has_clob_creds:
            raise ConfigurationError(
                "CLOB credentials missing. Run `derive-creds` to populate them in .env."
            )
        creds = ApiCreds(
            api_key=settings.clob_api_key.get_secret_value(),
            api_secret=settings.clob_secret.get_secret_value(),
            api_passphrase=settings.clob_passphrase.get_secret_value(),
        )
        client = ClobClient(
            host=settings.clob_host,
            chain_id=CHAIN_ID,
            key=settings.private_key.get_secret_value(),
            creds=creds,
            signature_type=SIGNATURE_TYPE,
            funder=settings.funder_address,
        )
        return cls(client)

    # --- Authenticated reads ---
    async def balance_allowance(self, *, conditional_token_id: str | None = None) -> Any:
        if conditional_token_id:
            params = BalanceAllowanceParams(
                asset_type=AssetType.CONDITIONAL,
                token_id=conditional_token_id,
                signature_type=SIGNATURE_TYPE,
            )
        else:
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                token_id=None,
                signature_type=SIGNATURE_TYPE,
            )
        return _to_plain(await asyncio.to_thread(self._client.get_balance_allowance, params))

    async def open_orders(self, *, market: str | None = None, asset_id: str | None = None) -> Any:
        """List open orders.

        The CLOB's open-orders endpoint is eventually consistent and, for
        deposit-wallet accounts, returns nothing for an unfiltered query: pass a
        ``market`` (condition id) or ``asset_id`` (token id) to reliably get
        results.
        """
        params = OpenOrderParams(market=market, asset_id=asset_id)
        return _to_plain(await asyncio.to_thread(self._client.get_open_orders, params))

    async def trades(self) -> Any:
        return _to_plain(await asyncio.to_thread(self._client.get_trades))

    # --- Writes ---
    async def place_order(self, req: PlaceOrderRequest) -> Any:
        """Sign and post an order. Returns the raw upstream response as a dict."""
        if req.order_type in (OrderType.FOK, OrderType.FAK) and req.price is None:
            raise ConfigurationError(
                "FOK/FAK orders need an explicit limit price in this MVP "
                "(marketable limit). Provide `price`."
            )
        options = await self._resolve_options(req)
        order_args = OrderArgsV2(
            token_id=req.token_id,
            price=float(req.price),
            size=float(req.size),
            side=Side(req.side).value,
            expiration=int(req.expiration or 0),
            builder_code="",
            metadata="",
            user_usdc_balance=None,
        )
        sdk_order_type = req.order_type.value
        try:
            resp = await asyncio.to_thread(
                self._client.create_and_post_order, order_args, options, sdk_order_type
            )
        except Exception as exc:  # SDK raises various exception types
            raise UpstreamError(_upstream_message("Order rejected", exc), code="clob_error") from exc
        return _to_plain(resp)

    async def cancel_order(self, order_id: str) -> Any:
        try:
            resp = await asyncio.to_thread(
                self._client.cancel_order, OrderPayload(orderID=order_id)
            )
        except Exception as exc:
            raise UpstreamError(_upstream_message("Cancel failed", exc), code="clob_error") from exc
        return _to_plain(resp)

    async def cancel_all(self) -> Any:
        try:
            resp = await asyncio.to_thread(self._client.cancel_all)
        except Exception as exc:
            raise UpstreamError(_upstream_message("Cancel-all failed", exc), code="clob_error") from exc
        return _to_plain(resp)

    async def _resolve_options(self, req: PlaceOrderRequest) -> PartialCreateOrderOptions:
        """Fill in tick size / neg-risk from the market when not supplied."""
        tick = req.tick_size
        neg = req.neg_risk
        if tick is None:
            try:
                tick = await asyncio.to_thread(self._client.get_tick_size, req.token_id)
            except Exception:  # noqa: BLE001 - non-fatal; SDK will use its default
                tick = None
        if neg is None:
            try:
                neg = await asyncio.to_thread(self._client.get_neg_risk, req.token_id)
            except Exception:  # noqa: BLE001
                neg = None
        return PartialCreateOrderOptions(
            tick_size=str(tick) if tick is not None else None,
            neg_risk=bool(neg) if neg is not None else None,
        )
