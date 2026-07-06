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
          "args": ["--from", "git+https://github.com/ilmari99/polygate@v0.5.0", "polygate-mcp"],
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

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any

from pydantic import ValidationError as PydanticValidationError

from . import __version__
from .config import Settings, get_settings
from .core import logging as core_logging
from .core.errors import PlatformError
from .models.order import OrderType, PlaceOrderRequest, Side
from .onboarding import complete_onboarding
from .services.facade import PolymarketService

log = logging.getLogger("polygate.mcp")

# Process-wide service, built once during the server lifespan.
_service: PolymarketService | None = None


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

    Reuses the shared onboarding routine with ``persist=False``: unlike the REST
    server nothing is written to ``.env``, because the wallet always comes from
    the environment, so deriving fresh each start keeps credentials consistent
    after a key swap (explicit ``CLOB_*`` / ``SIGNATURE_TYPE`` env overrides are
    respected). A failure is non-fatal - research tools still work - so we log
    and continue rather than crash the stdio server.
    """
    try:
        await complete_onboarding(settings, persist=False)
    except Exception as exc:  # noqa: BLE001 - never crash the MCP server on onboarding
        log.warning(
            "Wallet onboarding incomplete (%s); trading tools inactive until the "
            "wallet/connectivity is fixed. Research tools still work.",
            exc,
        )


def _require_service() -> PolymarketService:
    if _service is None:  # pragma: no cover - lifespan always sets it
        raise RuntimeError("PolyGate service is not initialised.")
    return _service


async def _serialize(awaitable: Any) -> dict[str, Any]:
    """Await a core operation and return a JSON-able dict for the MCP host.

    Maps a handled :class:`PlatformError` to ``{error, detail}`` so the model
    always receives a structured result instead of an opaque exception, and dumps
    pydantic results (response envelopes, order results) to plain JSON types.
    """
    try:
        result = await awaitable
    except PlatformError as exc:
        return {"error": exc.code, "detail": exc.message}
    return result.model_dump(mode="json")


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


def _load_trading_guide() -> str:
    """Read the packaged full trading briefing (``llm.md``)."""
    return files("polygate").joinpath("llm.md").read_text(encoding="utf-8")


# Condensed always-on briefing delivered to the host via the MCP ``instructions``
# field (the one channel most hosts inject automatically). It covers only the
# platform/API fundamentals; the full trading guide (strategy, memory, tool
# catalogue) is served on demand by the ``polygate://trading-guide`` resource below.
INSTRUCTIONS = """\
PolyGate exposes Polymarket prediction markets to MCP hosts as tools. This covers the
platform fundamentals; the full trading guide is served as the `polygate://trading-guide`
resource.

REAL money. Once a funded wallet is configured, `place_order` spends real funds on the
user's account. Confirm side, size, price, and cost with the user before ordering,
unless told to trade autonomously.

Positions. A share pays $1 if its outcome resolves true and $0 if false, so its price is
the market's implied probability (Yes at 0.62 = 62%). You need not hold to resolution:
sell any time at the current bid.

Ids. event id -> `get_comments`; conditionId (0x...) -> `get_market`, `get_holders`;
clobTokenId -> the book/order/trade tools. Prices and orders are ALWAYS per outcome
token, never per market. `outcomes`, `outcomePrices`, `clobTokenIds` are index-aligned.

Structure. A `market` is the atomic tradable (one `conditionId`, its `clobTokenIds`). An
`event` groups markets (a "market page"). Over events sit two parallel groupings: `tags`
(flat categories) and `series` (recurring/multi-part sets - each Fed decision, a monthly
BTC strike ladder, a tournament's fixtures). `gameId` is a sports-only event attribute, not
a level. Polymarket splits one topic across several separate sibling events, so `search`
and a single event show only a fragment. Navigate by deepening (`list_tags`/`list_series`
-> `list_events(tag_id=/series_id=)` -> `get_event` -> markets) or flatten every atomic
market under one scope with `collect_markets(series_id=|tag_id=|event=)`; for a sports game
use `collect_markets(event=<slug>, group_by="gameId")` to gather all its sub-markets.

The `side` footgun. `get_order_book` returns a `summary` with `best_bid`, `best_ask`,
`midpoint`, and `spread`. To buy you pay the `best_ask`; to sell you get the `best_bid`;
use `midpoint` for fair value.

Tradeable only when `active` is true and `closed` is false, `acceptingOrders` and
`enableOrderBook` are true, and `endDate` is in the future (re-check on the object).

Fees. Only takers (spread-crossing orders) pay: fee = shares * rate * p * (1 - p),
largest near p = 0.5; makers pay nothing and some markets are fee-free. Check a market's
`makerBaseFee`/`takerBaseFee`/`feesEnabled`.

Numbers. CLOB values (price, midpoint, spread, book) are strings - coerce before math.
`get_balance` is a raw 6-decimal integer string (divide by 1,000,000 for USDC); portfolio
and position dollar fields are already dollars. A marketable order must be worth >= $1.00
(`size * price`) and land on clean cents (whole shares on a 0.01-tick market).
"""


mcp = FastMCP(
    "polygate",
    instructions=INSTRUCTIONS,
    lifespan=_lifespan,
)
# FastMCP doesn't expose the low-level server's version, so it otherwise defaults
# to the MCP SDK's own version in the initialize handshake (a host would show
# e.g. "polygate 1.28.0"). Set it to our package version so serverInfo is right.
mcp._mcp_server.version = __version__


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@mcp.tool()
async def health() -> dict[str, Any]:
    """Liveness, version, run mode, and the active (secret-free) configuration.

    Reports `status`/`version`/`can_trade_live` plus the full config summary
    (mode, wallet, CLOB creds, hosts).
    """
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "can_trade_live": settings.can_trade_live,
        **settings.public_summary(),
    }


@mcp.resource("polygate://trading-guide", mime_type="text/markdown")
def trading_guide() -> str:
    """Full PolyGate trading briefing (llm.md): what a position is, the decision
    principles (q vs p, Kelly, Bayes, fees), common footguns, the tool catalogue,
    and memory discipline. The server `instructions` are a condensed version of this.
    """
    return _load_trading_guide()


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
    compact: bool = True,
) -> dict[str, Any]:
    """List markets (Gamma). Pass `slug` to fetch one market by its slug.

    Each market carries a `conditionId` and index-aligned `outcomes`,
    `outcomePrices` (each price is the implied probability), and `clobTokenIds`
    (the Yes/No token ids you trade on). Sort with `order` (e.g. 'volume24hr',
    'liquidity') plus `ascending`; a `limit` over 100 is paged automatically past
    Gamma's 100-row cap. Returns compact rows by default (low-signal fields -
    descriptions, images, AMM internals - are dropped); pass `compact=False` for
    the full objects.
    """
    return await _serialize(
        _require_service().list_markets(
            active=active,
            closed=closed,
            tag_id=tag_id,
            slug=slug,
            limit=limit,
            offset=offset,
            order=order,
            ascending=ascending,
            compact=compact,
        )
    )


@mcp.tool()
async def get_market(condition_id: str, compact: bool = True) -> dict[str, Any]:
    """Fetch a single market by its `conditionId` (0x...), as one object.

    Returns the market object (not a list); raises `not_found` if the id resolves
    to nothing. The full object to read before trading: `description`/
    `resolutionSource` (the exact resolution criteria), the tradeable flags
    (`active`, `closed`, `acceptingOrders`, `enableOrderBook`), `endDate`, and the
    fee params (`makerBaseFee`/`takerBaseFee` in basis points, `feesEnabled`).
    Compact by default; pass `compact=False` for every field.
    """
    return await _serialize(_require_service().get_market(condition_id, compact=compact))


@mcp.tool()
async def list_events(
    active: bool = True,
    closed: bool = False,
    tag_id: int | None = None,
    series_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
    compact: bool = True,
) -> dict[str, Any]:
    """List events (each event groups one or more markets).

    `tag_id` drills into a category; `series_id` drills into a series (a
    recurring/multi-part group - each Fed decision, a monthly BTC strike ladder,
    a tournament's fixtures). A `limit` over 100 is paged automatically past
    Gamma's per-page cap. Compact by default (low-signal fields dropped and the
    nested markets compacted); pass `compact=False` for full objects.
    """
    return await _serialize(
        _require_service().list_events(
            active=active,
            closed=closed,
            tag_id=tag_id,
            series_id=series_id,
            limit=limit,
            offset=offset,
            order=order,
            compact=compact,
        )
    )


@mcp.tool()
async def get_event(key: str, compact: bool = True) -> dict[str, Any]:
    """Fetch a single event (with its nested markets) by slug or event id.

    An event is a 'market page' grouping one or more atomic markets. Use this to
    resolve a slug/id you got from `search` or `list_events` into the full object,
    then read its `series`/`gameId` to navigate to related events. Compact by
    default; pass `compact=False` for every field.
    """
    return await _serialize(_require_service().get_event(key, compact=compact))


@mcp.tool()
async def list_series(limit: int = 100, offset: int = 0, compact: bool = True) -> dict[str, Any]:
    """List series - Polymarket's recurring/multi-part groupings of events.

    Examples: `fomc` (each Fed decision), `cpi`, `btc-multi-strikes-weekly`,
    `nyc-daily-weather`, a sports league or tournament. A lightweight catalog:
    each entry carries an `event_count` instead of its events. Drill into a
    series with `list_events(series_id=...)` or flatten it with
    `collect_markets(series_id=...)`.
    """
    return await _serialize(
        _require_service().list_series(limit=limit, offset=offset, compact=compact)
    )


@mcp.tool()
async def collect_markets(
    series_id: int | None = None,
    tag_id: int | None = None,
    event: str | None = None,
    group_by: str | None = None,
    active: bool = True,
    closed: bool = False,
    compact: bool = True,
) -> dict[str, Any]:
    """Flatten every atomic market under one grouping node into a single flat list.

    Polymarket buries related markets across separate sibling events, so `search`
    and drilling into one event show only a fragment. This gathers them all. Pass
    EXACTLY ONE scope:
    - `series_id` - every market in that series' events.
    - `tag_id` - every market in that category's events.
    - `event` (slug or id) - that event's markets; add `group_by="gameId"` to
      expand a sports fixture to all its sibling events (moneyline, spread,
      totals, ...) and collect their markets. `group_by` names any event
      attribute - no key is hardcoded, so a future non-sports link key works too.

    Each returned market is tagged with its parent `event_id`/`event_title`/
    `event_slug` and carries its `conditionId` and `clobTokenIds`. The scan runs
    to completion and errors if the scope is too broad - never silently partial.
    Compact by default; pass `compact=False` for full objects.
    """
    return await _serialize(
        _require_service().collect_markets(
            series_id=series_id,
            tag_id=tag_id,
            event=event,
            group_by=group_by,
            active=active,
            closed=closed,
            compact=compact,
        )
    )


@mcp.tool()
async def list_tags() -> dict[str, Any]:
    """List the category tags markets can be filtered by."""
    return await _serialize(_require_service().list_tags())


@mcp.tool()
async def get_order_book(token_id: str) -> dict[str, Any]:
    """Full CLOB order book for an outcome token (`clobTokenId`).

    Carries a derived `summary` computed from the ladder: `best_bid`, `best_ask`
    (with sizes), `midpoint` (fair value), and `spread`.
    To buy you pay `best_ask`; to sell you get `best_bid`.
    """
    return await _serialize(_require_service().order_book(token_id))


@mcp.tool()
async def get_last_trade_price(token_id: str) -> dict[str, Any]:
    """Last traded price for an outcome token - live CLOB, more current than Gamma's cached `bestBid`/`bestAsk`."""
    return await _serialize(_require_service().last_trade_price(token_id))


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
    minutes. If you pass none of these it defaults to a 1-week window at hourly
    resolution; the applied `interval` is echoed back in the payload so you know
    the span you received.
    """
    return await _serialize(
        _require_service().prices_history(
            token_id, interval=interval, start_ts=start_ts, end_ts=end_ts, fidelity=fidelity
        )
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
    compact: bool = True,
    flatten: bool = False,
) -> dict[str, Any]:
    """Full-text search over Polymarket events and markets.

    Results group under `events`; each event's nested markets already carry the
    decoded `clobTokenIds` (an array, like `outcomes`/`outcomePrices`) you trade
    on. `limit_per_type` bounds the number of events, not the markets nested in
    each, so a few hits can still be a large payload. Polymarket splits one topic
    across sibling events (a game's moneyline, spreads, exact-score, ... are
    separate events), so search shows only a fragment: to gather every market for
    a fixture, pass its slug to `collect_markets(event=<slug>, group_by="gameId")`.
    Set `flatten=True` to also get a top-level `markets` array (each entry tagged
    with `event_id`/`event_title`/`event_slug`) - off by default because it
    duplicates every nested market and roughly doubles the payload. `events_status`
    may be e.g. 'active' or 'resolved'. Compact by default; pass `compact=False`
    for full objects.
    """
    return await _serialize(
        _require_service().search(
            q,
            limit_per_type=limit_per_type,
            page=page,
            events_status=events_status,
            compact=compact,
            flatten=flatten,
        )
    )


@mcp.tool()
async def get_comments(
    event_id: int,
    limit: int = 50,
    offset: int = 0,
    order: str | None = None,
    ascending: bool | None = None,
) -> dict[str, Any]:
    """Public comments on an event (by its numeric event id). Unverified sentiment."""
    return await _serialize(
        _require_service().comments(
            event_id, limit=limit, offset=offset, order=order, ascending=ascending
        )
    )


@mcp.tool()
async def get_holders(condition_id: str, limit: int = 100) -> dict[str, Any]:
    """Top holders for a market (`conditionId`), grouped per outcome token.

    Each holder has `amount`, `outcomeIndex`, `proxyWallet`, `pseudonym` - a
    signal about how much money sits on each side.
    """
    return await _serialize(_require_service().holders(condition_id, limit=limit))


# --------------------------------------------------------------------------- #
# Portfolio / account (require a configured wallet)
# --------------------------------------------------------------------------- #
@mcp.tool()
async def get_positions(limit: int = 100, compact: bool = True) -> dict[str, Any]:
    """Open positions for the configured wallet. Requires a wallet.

    Eventually consistent: right after a fill it may lag, so re-poll rather than
    trust an empty result. Compact by default (drops the `icon` url); pass
    `compact=False` for every field.
    """
    return await _serialize(_require_service().positions(limit=limit, compact=compact))


@mcp.tool()
async def get_portfolio_value() -> dict[str, Any]:
    """Current portfolio value (USD) for the configured wallet. Requires a wallet."""
    return await _serialize(_require_service().portfolio_value())


@mcp.tool()
async def get_balance(token_id: str | None = None) -> dict[str, Any]:
    """Collateral (USDC) balance, or a conditional-token balance when `token_id` is set.

    USDC balances are raw 6-decimal integer strings: divide by 1,000,000 for
    dollars. The per-contract `allowances` are collapsed to `"unlimited"` where
    Polymarket has granted the max approval (a finite value would mean trading is
    capped/blocked). Requires a configured wallet and CLOB credentials.
    """
    return await _serialize(_require_service().balance(token_id=token_id))


@mcp.tool()
async def get_activity(limit: int = 100, compact: bool = True) -> dict[str, Any]:
    """Account activity feed for the configured wallet. Requires a wallet.

    Compact by default (drops the wallet's own profile/identity noise - `icon`,
    `name`, `pseudonym`, `bio`, `profileImage*`); pass `compact=False` for every
    field.
    """
    return await _serialize(_require_service().activity(limit=limit, compact=compact))


@mcp.tool()
async def get_open_orders(
    market: str | None = None, asset_id: str | None = None
) -> dict[str, Any]:
    """Open orders for the configured wallet.

    The CLOB may return nothing for a fully unfiltered query, so pass a `market`
    (condition id) or `asset_id` (token id) to list reliably.
    """
    return await _serialize(
        _require_service().open_orders(market=market, asset_id=asset_id)
    )


@mcp.tool()
async def get_trades(limit: int = 100, compact: bool = True) -> dict[str, Any]:
    """Trade history for the configured wallet, newest-first. Requires a wallet.

    Bounded to `limit` trades. Compact by default (projects each fill to its
    high-signal fields - id, market, asset_id, side, size, price, outcome,
    status, match_time, fee_rate_bps, trader_side - dropping nested maker_orders,
    hashes and owner ids); pass `compact=False` for the full fills.
    """
    return await _serialize(_require_service().trades(limit=limit, compact=compact))


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

    For an instant taker fill, cross the book: to buy set price >= best ask, to
    sell set price <= best bid - takers pay a fee, makers don't. A marketable
    order must be worth >= $1.00 (`size * price`) and land on clean cents (on a
    0.01-tick market use whole-share counts). The result carries `order_id` and
    `status` ('live' or 'matched').
    """
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
    except PydanticValidationError as exc:
        # Summarize to "field: reason" pairs so the model gets an actionable
        # message instead of pydantic's multi-line dump with doc URLs.
        detail = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}"
            for e in exc.errors()
        )
        return {"error": "validation_error", "detail": detail}
    except ValueError as exc:
        return {"error": "validation_error", "detail": str(exc)}
    return await _serialize(_require_service().place_order(req))


@mcp.tool()
async def cancel_order(order_id: str) -> dict[str, Any]:
    """Cancel a single open order by its order id."""
    return await _serialize(_require_service().cancel_order(order_id))


@mcp.tool()
async def cancel_all_orders() -> dict[str, Any]:
    """Cancel all open orders for the configured wallet."""
    return await _serialize(_require_service().cancel_all())


def run() -> None:
    """Console-script entrypoint: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
