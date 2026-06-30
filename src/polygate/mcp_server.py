"""Model Context Protocol (MCP) server exposing PolyGate to any MCP host.

This wraps the same :class:`~polygate.services.facade.PolymarketService` that
backs the REST gateway, but speaks MCP over stdio instead of HTTP - so any
MCP-capable AI application (Claude Desktop, IDE assistants, custom agents, ...)
can research Polymarket and place trades with a single ``mcpServers`` entry and
no separate server process, port, or platform API key.

Configure your MCP host like::

    {
      "mcpServers": {
        "polygate": {
          "command": "uvx",
          "args": ["--from", "git+https://github.com/ilmari99/polygate@v0.1.0", "polygate-mcp"],
          "env": {
            "FUNDER_ADDRESS": "0xYourWalletAddress...",
            "PRIVATE_KEY": "0xYourPrivateKey..."
          }
        }
      }
    }

Market-data and research tools work with no wallet. The account and trading tools
become active once ``PRIVATE_KEY`` and ``FUNDER_ADDRESS`` are provided; the CLOB
credentials and order signature type are derived automatically in memory at
startup. **Orders are real money once a funded wallet is configured.**
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from pydantic import SecretStr

from . import __version__
from .config import Settings, get_settings
from .core import logging as core_logging
from .core.errors import PlatformError
from .core.sigtype import detect_signature_type
from .models.order import OrderType, PlaceOrderRequest, Side
from .services.credentials import derive_clob_credentials
from .services.facade import PolymarketService

log = logging.getLogger("polygate.mcp")

# Process-wide service, built once during the server lifespan.
_service: PolymarketService | None = None


def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _configure_stderr_logging(level: str = "INFO") -> None:
    """Route all PolyGate logging to stderr.

    A stdio MCP server speaks JSON-RPC on **stdout**; anything else written there
    corrupts the stream. PolyGate's default :func:`core.logging.configure_logging`
    logs to stdout, so we install our own stderr handler instead and mark the
    shared logging module as configured to stop it ever attaching a stdout one.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("polygate")
    root.setLevel(level.upper())
    # Avoid duplicate handlers if this runs twice (e.g. in tests).
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False
    # Block core.logging.configure_logging() from adding a stdout handler later.
    core_logging._CONFIGURED = True  # type: ignore[attr-defined]


async def _onboard(settings: Settings) -> None:
    """Make the wallet trade-ready in memory (derive creds, detect sig type).

    Unlike the REST server's onboarding, nothing is persisted: the wallet always
    comes from the environment, so deriving the CLOB credentials freshly on each
    start keeps them consistent with the current wallet and avoids a stale
    credential ever being used after the user swaps ``PRIVATE_KEY``.
    Explicit ``CLOB_*`` / ``SIGNATURE_TYPE`` environment overrides are respected.
    """
    if settings.dry_run or not settings.has_wallet:
        return

    clob_overridden = all(
        os.environ.get(name)
        for name in ("CLOB_API_KEY", "CLOB_SECRET", "CLOB_PASSPHRASE")
    )
    if not clob_overridden:
        try:
            creds = await asyncio.to_thread(derive_clob_credentials, settings)
        except Exception as exc:  # noqa: BLE001 - surface, don't crash the server
            log.warning(
                "Could not derive CLOB credentials (%s); trading tools will be "
                "inactive until the wallet/connectivity is fixed. Research tools "
                "still work.",
                exc,
            )
            return
        settings.clob_api_key = SecretStr(creds["CLOB_API_KEY"])
        settings.clob_secret = SecretStr(creds["CLOB_SECRET"])
        settings.clob_passphrase = SecretStr(creds["CLOB_PASSPHRASE"])
        log.info("Derived CLOB credentials for the configured wallet.")

    if settings.signature_type is None and settings.has_clob_creds:
        try:
            detected = await asyncio.to_thread(detect_signature_type, settings)
        except Exception as exc:  # noqa: BLE001 - detection must never be fatal
            log.warning("Signature-type detection failed (%s); using default.", exc)
            detected = None
        if detected is not None:
            settings.signature_type = detected
            log.info("Detected order signature type %d.", detected)


def _require_service() -> PolymarketService:
    if _service is None:  # pragma: no cover - lifespan always sets it
        raise RuntimeError("PolyGate service is not initialised.")
    return _service


async def _data(source: str, awaitable: Any) -> dict[str, Any]:
    """Await an upstream read and wrap it like the REST data envelope.

    Returns ``{data, fetched_at, source}`` on success or ``{error, detail}`` on a
    handled :class:`PlatformError`, so the model always receives a structured,
    machine-readable result instead of an opaque exception.
    """
    try:
        data = await awaitable
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return {"data": data, "fetched_at": _utcnow_iso(), "source": source}


def _flatten_search(data: Any) -> Any:
    """Surface a flat ``markets`` list on search results (mirrors the REST route).

    Gamma groups markets under events, so token ids live at
    ``events[].markets[].clobTokenIds``. We also expose a top-level ``markets``
    array (each entry tagged with its parent ``event_id``/``event_title``) so a
    caller can read ``clobTokenIds`` directly.
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


@asynccontextmanager
async def _lifespan(_server: "FastMCP") -> AsyncIterator[None]:
    """Build the PolyGate service once, tear it down on shutdown."""
    global _service
    get_settings.cache_clear()
    settings = get_settings()
    _configure_stderr_logging(settings.log_level)
    if settings.dry_run:
        log.warning("DRY_RUN enabled: orders are simulated, not sent.")
    elif not settings.has_wallet:
        log.warning(
            "No wallet configured; account and trading tools are inactive. Set "
            "PRIVATE_KEY and FUNDER_ADDRESS to enable them. "
            "Market-data and research tools work without a wallet."
        )
    await _onboard(settings)
    _service = PolymarketService(settings)
    log.info("PolyGate MCP server ready (version %s).", __version__)
    try:
        yield
    finally:
        await _service.aclose()
        _service = None
        log.info("PolyGate MCP server stopped.")


# Importing FastMCP here keeps the import error (if `mcp` is missing) close to the
# code that needs it, with a clear remediation hint.
try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise ModuleNotFoundError(
        "The 'mcp' package is required for the PolyGate MCP server. Install it "
        "with `pip install mcp` (or reinstall polygate, which depends on it)."
    ) from exc


mcp = FastMCP(
    "polygate",
    instructions=(
        "PolyGate exposes Polymarket prediction markets to MCP hosts. Discover "
        "markets with `search` or `list_markets`; read live prices with "
        "`get_order_book`, `get_price`, `get_midpoint`; inspect the account with "
        "`get_positions`, `get_portfolio_value`, `get_balance`; and trade with "
        "`place_order` / `cancel_order`. Prices and orders are always per outcome "
        "token (`clobTokenId`), never per market. Trades use REAL money once a "
        "funded wallet is configured - confirm side, size, price and cost with "
        "the user before calling `place_order`. The `get_price` side argument "
        "returns the best price on that side of the book: to BUY query side=SELL "
        "(the ask), to SELL query side=BUY (the bid); use `get_midpoint` for fair "
        "value."
    ),
    lifespan=_lifespan,
)


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@mcp.tool()
async def health() -> dict[str, Any]:
    """Liveness and current run mode. Shows whether a wallet is configured."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "mode": "dry-run" if settings.dry_run else "live",
        "configured": settings.has_wallet,
        "wallet_address": settings.funder_address,
        "can_trade_live": settings.can_trade_live,
    }


@mcp.tool()
async def config() -> dict[str, Any]:
    """Secret-free summary of the active configuration (mode, wallet, hosts)."""
    return get_settings().public_summary()


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@mcp.tool()
async def list_markets(
    active: bool = True,
    closed: bool = False,
    tag_id: int | None = None,
    slug: str | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
    ascending: bool | None = None,
) -> dict[str, Any]:
    """List markets (Gamma). Pass `slug` to fetch one market by its slug.

    Each market carries a `conditionId` and a JSON-encoded `clobTokenIds` array
    (the Yes/No outcome token ids you trade on).
    """
    service = _require_service()
    if slug:
        return await _data("gamma", service.gamma.get_market_by_slug(slug))
    return await _data(
        "gamma",
        service.gamma.list_markets(
            active=active,
            closed=closed,
            tag_id=tag_id,
            limit=limit,
            offset=offset,
            order=order,
            ascending=ascending,
        ),
    )


@mcp.tool()
async def get_market(condition_id: str) -> dict[str, Any]:
    """Fetch a single market by its `conditionId` (0x...)."""
    return await _data("gamma", _require_service().gamma.get_market(condition_id))


@mcp.tool()
async def list_events(
    active: bool = True,
    closed: bool = False,
    tag_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
) -> dict[str, Any]:
    """List events (each event groups one or more markets)."""
    return await _data(
        "gamma",
        _require_service().gamma.list_events(
            active=active, closed=closed, tag_id=tag_id, limit=limit, offset=offset, order=order
        ),
    )


@mcp.tool()
async def list_tags() -> dict[str, Any]:
    """List the category tags markets can be filtered by."""
    return await _data("gamma", _require_service().gamma.list_tags())


@mcp.tool()
async def get_order_book(token_id: str) -> dict[str, Any]:
    """Full CLOB order book for an outcome token (`clobTokenId`).

    Bid/ask arrays are not guaranteed sorted: best bid is the max bid price, best
    ask is the min ask price.
    """
    return await _data("clob", _require_service().clob.order_book(token_id))


@mcp.tool()
async def get_price(token_id: str, side: str = "BUY") -> dict[str, Any]:
    """Best book price for an outcome token on one side.

    `side=BUY` returns the best bid, `side=SELL` the best ask. Note: the price you
    pay to BUY is the ask (query side=SELL); the price you get to SELL is the bid
    (query side=BUY).
    """
    return await _data("clob", _require_service().clob.price(token_id, side))


@mcp.tool()
async def get_midpoint(token_id: str) -> dict[str, Any]:
    """Order-book midpoint for an outcome token - a fair-value estimate."""
    return await _data("clob", _require_service().clob.midpoint(token_id))


@mcp.tool()
async def get_spread(token_id: str) -> dict[str, Any]:
    """Current bid/ask spread for an outcome token."""
    return await _data("clob", _require_service().clob.spread(token_id))


@mcp.tool()
async def get_last_trade_price(token_id: str) -> dict[str, Any]:
    """Last traded price for an outcome token."""
    return await _data("clob", _require_service().clob.last_trade_price(token_id))


@mcp.tool()
async def get_prices_history(
    token_id: str,
    interval: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    fidelity: int | None = None,
) -> dict[str, Any]:
    """Historical price series for an outcome token.

    Provide either `interval` (e.g. '1h', '6h', '1d', '1w', 'max') or a
    `start_ts`/`end_ts` Unix-seconds window. `fidelity` is the resolution in
    minutes.
    """
    return await _data(
        "clob",
        _require_service().clob.prices_history(
            token_id, interval=interval, start_ts=start_ts, end_ts=end_ts, fidelity=fidelity
        ),
    )


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #
@mcp.tool()
async def search(
    q: str,
    limit_per_type: int | None = None,
    page: int | None = None,
    events_status: str | None = None,
) -> dict[str, Any]:
    """Full-text search over Polymarket events and markets.

    Results group under `events`; a flat `markets` array is also returned (each
    entry tagged with `event_id`/`event_title`) so you can read `clobTokenIds`
    directly. `events_status` may be e.g. 'active' or 'resolved'.
    """
    service = _require_service()
    try:
        data = await service.gamma.search(
            q, limit_per_type=limit_per_type, page=page, events_status=events_status
        )
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return {"data": _flatten_search(data), "fetched_at": _utcnow_iso(), "source": "gamma"}


@mcp.tool()
async def get_comments(
    event_id: int,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
    ascending: bool | None = None,
) -> dict[str, Any]:
    """Public comments on an event (by its numeric event id). Unverified sentiment."""
    return await _data(
        "gamma",
        _require_service().gamma.list_comments(
            parent_entity_id=event_id, limit=limit, offset=offset, order=order, ascending=ascending
        ),
    )


@mcp.tool()
async def get_holders(condition_id: str, limit: int = 100) -> dict[str, Any]:
    """Top holders for a market (`conditionId`), grouped per outcome token.

    Each holder has `amount`, `outcomeIndex`, `proxyWallet`, `pseudonym` - a
    signal about how much money sits on each side.
    """
    return await _data("data", _require_service().data.holders(condition_id, limit=limit))


# --------------------------------------------------------------------------- #
# Portfolio / account (require a configured wallet)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_positions(limit: int = 100) -> dict[str, Any]:
    """Open positions for the configured wallet. Requires a wallet."""
    service = _require_service()
    try:
        user = service.require_funder()
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return await _data("data", service.data.positions(user, limit=limit))


@mcp.tool()
async def get_portfolio_value() -> dict[str, Any]:
    """Current portfolio value (USD) for the configured wallet. Requires a wallet."""
    service = _require_service()
    try:
        user = service.require_funder()
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return await _data("data", service.data.value(user))


@mcp.tool()
async def get_balance(token_id: str | None = None) -> dict[str, Any]:
    """Collateral (USDC) balance, or a conditional-token balance when `token_id` is set.

    USDC balances are raw 6-decimal integer strings: divide by 1,000,000 for
    dollars. Requires a configured wallet and CLOB credentials.
    """
    service = _require_service()
    try:
        result = await service.trading().balance_allowance(conditional_token_id=token_id)
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return {"data": result, "fetched_at": _utcnow_iso(), "source": "clob"}


@mcp.tool()
async def get_activity(limit: int = 100) -> dict[str, Any]:
    """Account activity feed for the configured wallet. Requires a wallet."""
    service = _require_service()
    try:
        user = service.require_funder()
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return await _data("data", service.data.activity(user, limit=limit))


@mcp.tool()
async def get_open_orders(
    market: str | None = None, asset_id: str | None = None
) -> dict[str, Any]:
    """Open orders for the configured wallet.

    The CLOB may return nothing for a fully unfiltered query, so pass a `market`
    (condition id) or `asset_id` (token id) to list reliably.
    """
    service = _require_service()
    try:
        result = await service.trading().open_orders(market=market, asset_id=asset_id)
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return {"data": result, "fetched_at": _utcnow_iso(), "source": "clob"}


@mcp.tool()
async def get_trades() -> dict[str, Any]:
    """Trade history for the configured wallet. Requires a wallet."""
    service = _require_service()
    try:
        result = await service.trading().trades()
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return {"data": result, "fetched_at": _utcnow_iso(), "source": "clob"}


# --------------------------------------------------------------------------- #
# Trading (REAL money once a funded wallet is configured)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def place_order(
    token_id: str,
    side: str,
    size: float,
    price: float | None = None,
    order_type: str = "GTC",
    expiration: int | None = None,
    tick_size: str | None = None,
    neg_risk: bool | None = None,
) -> dict[str, Any]:
    """Place an order on Polymarket. REAL money once a funded wallet is configured.

    Args:
        token_id: CLOB token id of the outcome (Yes or No), from `clobTokenIds`.
        side: 'BUY' or 'SELL'.
        size: Number of outcome shares (> 0).
        price: Limit price in (0, 1). Required for GTC/GTD, and for FOK/FAK.
        order_type: 'GTC' (default, resting limit), 'GTD' (needs `expiration`),
            'FOK' (fill-or-kill), 'FAK' (fill-and-kill).
        expiration: Unix seconds; required for GTD orders.
        tick_size: Market tick size, e.g. '0.01'. Auto-detected if omitted.
        neg_risk: Whether this is a neg-risk market. Auto-detected if omitted.

    For an instant taker fill: to buy set price >= best ask; to sell set
    price <= best bid.
    """
    service = _require_service()
    try:
        req = PlaceOrderRequest(
            token_id=token_id,
            side=Side(side.upper()),
            size=size,
            price=price,
            order_type=OrderType(order_type.upper()),
            expiration=expiration,
            tick_size=tick_size,
            neg_risk=neg_risk,
        )
    except ValueError as exc:
        return {"error": "validation_error", "detail": str(exc)}
    try:
        result = await service.place_order(req)
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return result.model_dump()


@mcp.tool()
async def cancel_order(order_id: str) -> dict[str, Any]:
    """Cancel a single open order by its order id."""
    service = _require_service()
    try:
        result = await service.cancel_order(order_id)
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return result.model_dump()


@mcp.tool()
async def cancel_all_orders() -> dict[str, Any]:
    """Cancel all open orders for the configured wallet."""
    service = _require_service()
    try:
        result = await service.cancel_all()
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return result.model_dump()


def run() -> None:
    """Console-script entrypoint: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
