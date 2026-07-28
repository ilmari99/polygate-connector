"""Model Context Protocol (MCP) server for read-only Polymarket research.

Wraps :class:`~polygate_connector.services.facade.PolymarketService` and exposes public
Polymarket data - events, markets, order books, prices, comments, holders - to
any MCP host. No account, credentials, or wallet are involved; every tool is
read-only.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from . import __version__
from .config import get_settings
from .core import logging as core_logging
from .core.errors import PlatformError
from .services.facade import PolymarketService

log = logging.getLogger("polygate_connector.mcp")

# Process-wide service, built once during the server lifespan.
_service: PolymarketService | None = None


def _configure_stderr_logging(level: str = "INFO") -> None:
    """Route all server logging to stderr.

    A stdio MCP server speaks JSON-RPC on **stdout**; anything else written there
    corrupts the stream. The package's default :func:`core.logging.configure_logging`
    logs to stdout, so we install our own stderr handler instead and mark the
    shared logging module as configured to stop it ever attaching a stdout one.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger("polygate_connector")
    root.setLevel(level.upper())
    # Avoid duplicate handlers if this runs twice (e.g. in tests).
    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.addHandler(handler)
    root.propagate = False
    # Block core.logging.configure_logging() from adding a stdout handler later.
    core_logging._CONFIGURED = True  # type: ignore[attr-defined]


def _require_service() -> PolymarketService:
    if _service is None:  # pragma: no cover - lifespan always sets it
        raise RuntimeError("Service is not initialised.")
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
    """Build the Polymarket service once, tear it down on shutdown."""
    global _service
    get_settings.cache_clear()
    settings = get_settings()
    _configure_stderr_logging(settings.log_level)
    _service = PolymarketService(settings)
    log.info("Polymarket research MCP server ready (version %s).", __version__)
    try:
        yield
    finally:
        await _service.aclose()
        _service = None
        log.info("Polymarket research MCP server stopped.")


# Importing FastMCP here keeps the import error (if `mcp` is missing) close to the
# code that needs it, with a clear remediation hint.
try:
    from mcp.server.fastmcp import FastMCP
except ModuleNotFoundError as exc:  # pragma: no cover - dependency guard
    raise ModuleNotFoundError(
        "The 'mcp' package is required for this MCP server. Install it "
        "with `pip install mcp` (or reinstall polygate-connector, which depends on it)."
    ) from exc


# Always-on briefing delivered to the host via the MCP ``instructions`` field.
# Factual platform/API fundamentals only - no behavioural guidance.
INSTRUCTIONS = """\
Polymarket prediction-market data: events, markets, order books, prices, and public
holder and comment data. Read-only.

Positions. A share pays $1 if its outcome resolves true and $0 if false, so its price is
the market's implied probability (Yes at 0.62 = 62%).

Ids. event id -> `get_comments`; conditionId (0x...) -> `get_market`, `get_holders`;
clobTokenId -> the book/price tools. Prices are ALWAYS per outcome token, never per
market. `outcomes`, `outcomePrices`, `clobTokenIds` are index-aligned.

Structure. A `market` is the atomic tradable (one `conditionId`, its `clobTokenIds`). An
`event` groups markets (a "market page"). Over events sit two parallel groupings: `tags`
(flat categories) and `series` (recurring/multi-part sets - each Fed decision, a monthly
BTC strike ladder, a tournament's fixtures). `gameId` is a sports-only event attribute, not
a level. Polymarket splits one topic across several separate sibling events, so `search`
and a single event show only a fragment. Navigate by deepening (`list_tags`/`list_series`
-> `list_events(tag_id=/series_id=)` -> `get_event` -> markets) or flatten every atomic
market under one scope with `collect_markets(series_id=|tag_id=|event=)`; for a sports game
use `collect_markets(event=<slug>, group_by="gameId")` to gather all its sub-markets.

Order books. `get_order_book` returns a `summary` with `best_bid`, `best_ask`,
`midpoint`, and `spread`. The `best_ask` is the price a buyer pays; the `best_bid` is
the price a seller receives; `midpoint` sits between them.

A market is open for trading on Polymarket when `active` is true, `closed` is false,
`acceptingOrders` and `enableOrderBook` are true, and `endDate` is in the future.

Numbers. CLOB values (price, midpoint, spread, book) are strings - coerce before math.
"""


mcp = FastMCP(
    "polymarket-research",
    instructions=INSTRUCTIONS,
    lifespan=_lifespan,
    stateless_http=True,
)
# FastMCP doesn't expose the low-level server's version, so it otherwise defaults
# to the MCP SDK's own version in the initialize handshake (a host would show
# e.g. "polymarket-research 1.28.0"). Set it to our package version so serverInfo
# is right.
mcp._mcp_server.version = __version__


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@mcp.tool()
async def health() -> dict[str, Any]:
    """Liveness check: server status, version, and the upstream API hosts in use."""
    settings = get_settings()
    return {
        "status": "ok",
        "version": __version__,
        "server": mcp.name,
        "hosts": {
            "gamma": settings.gamma_host,
            "clob": settings.clob_host,
            "data": settings.data_host,
        },
    }


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


def run() -> None:
    """Console-script entrypoint: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
