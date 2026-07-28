"""Model Context Protocol (MCP) server for read-only Polymarket research.

Wraps :class:`~polygate_connector.services.facade.PolymarketService` and exposes public
Polymarket data - events, markets, order books, prices, comments, holders - to
any MCP host. No account, credentials, or wallet are involved; every tool is
read-only.
"""

from __future__ import annotations

import logging
import sys
import time
from collections.abc import AsyncIterator, Awaitable
from contextlib import asynccontextmanager
from importlib.resources import files
from typing import Any, Literal

from . import __version__
from .config import get_settings
from .core import logging as core_logging
from .core.errors import PlatformError
from .render import (
    HOLDER_COLUMNS,
    MARKET_COLUMNS,
    SEARCH_COLUMNS,
    SERIES_COLUMNS,
    Column,
    enforce_size_cap,
    markdown_table,
    payload_bytes,
)
from .services.facade import PolymarketService

Verbosity = Literal["minimal", "compact", "full"]

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


async def _run_tool(
    name: str, awaitable: Awaitable[Any], *, table: list[Column] | None = None
) -> dict[str, Any]:
    """Await a tool operation and always return a JSON-able dict.

    A :class:`PlatformError` surfaces as its stable ``code`` with an actionable
    ``detail``; any other exception becomes ``internal_error`` - the traceback
    is logged server-side and never sent to the client. One log line per call
    records tool name, duration, status, response size, and row count - the
    usage-pattern signal - and never arguments or result contents, matching
    the privacy policy.

    When ``table`` is given and the result carries list ``rows``, they are
    rendered as a markdown table (same information, roughly half the tokens of
    key-repeated JSON). Every result then passes the response size cap.
    """
    started = time.perf_counter()
    status = "ok"
    result_bytes: int | None = None
    rows_returned: Any = None
    try:
        # Serialization, table rendering, and the size cap stay inside the
        # try: a failure in any of them must surface as a typed error too,
        # and must not be logged as ok.
        result = await awaitable
        if not isinstance(result, dict):
            result = result.model_dump(mode="json", exclude_none=True)
        if table is not None and isinstance(result.get("rows"), list):
            result["rows"] = markdown_table(result["rows"], table)
        final = enforce_size_cap(result)
        result_bytes = payload_bytes(final)
        rows_returned = final.get("returned")
        return final
    except PlatformError as exc:
        status = exc.code
        return {"error": exc.code, "detail": exc.message}
    except Exception:  # noqa: BLE001 - a bare 500 to the client fails review
        status = "internal_error"
        log.exception("Tool %s failed unexpectedly.", name)
        return {
            "error": "internal_error",
            "detail": f"{name} hit an unexpected server error. Retry; if it "
            "persists, report it on the support channel.",
        }
    finally:
        log.info(
            "tool=%s duration_ms=%.0f status=%s bytes=%s returned=%s",
            name,
            (time.perf_counter() - started) * 1000,
            status,
            "-" if result_bytes is None else result_bytes,
            "-" if rows_returned is None else rows_returned,
        )


@asynccontextmanager
async def service_context() -> AsyncIterator[PolymarketService]:
    """Own the process-wide service for the duration of the context.

    If a service is already installed (e.g. by the HTTP entry point, whose app
    lifespan outlives the per-session MCP lifespan), it is reused untouched -
    building one per stateless HTTP session would discard the response cache
    and the connection pool on every request.
    """
    global _service
    if _service is not None:
        yield _service
        return
    get_settings.cache_clear()
    settings = get_settings()
    _configure_stderr_logging(settings.log_level)
    _service = PolymarketService(settings)
    log.info("Polymarket research MCP server ready (version %s).", __version__)
    try:
        yield _service
    finally:
        await _service.aclose()
        _service = None
        log.info("Polymarket research MCP server stopped.")


@asynccontextmanager
async def _lifespan(_server: "FastMCP") -> AsyncIterator[None]:
    """Build the Polymarket service once, tear it down on shutdown."""
    async with service_context():
        yield


# Importing FastMCP here keeps the import error (if `mcp` is missing) close to the
# code that needs it, with a clear remediation hint.
try:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations
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

Ids. event id or slug -> `get_event`, `get_comments`; conditionId (0x...) ->
`get_market`, `get_holders`; clobTokenId (on a market's `clobTokenIds`, from
`get_market` or `get_event`) -> the book/price tools. Prices are ALWAYS per outcome
token, never per market. `outcomes`, `outcomePrices`, `clobTokenIds` are index-aligned.

Structure. A `market` is one question (one `conditionId`). An `event` groups markets (a
"market page"). Over events sit two parallel groupings: `tags` (flat categories) and
`series` (recurring/multi-part sets - each Fed decision, a monthly BTC strike ladder, a
tournament's fixtures). `gameId` is a sports-only event attribute, not a level.
Polymarket splits one topic across several separate sibling events, so `search` and a
single event show only a fragment of a topic. `list_events(tag_id=|series_id=)` lists a
grouping's events; `collect_markets(series_id=|tag_id=|event=)` returns every atomic
market under one scope as a flat list, and with `group_by="gameId"` gathers a sports
fixture's sub-markets across its sibling events.

Pages. List tools return one page of `rows` plus `next_offset`/`next_page` when more
rows exist; at the default verbosity ("minimal") some tools format rows as a markdown
table. `verbosity="compact"` returns fuller objects and `"full"` the raw upstream ones.

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


def read_tool(title: str, *, open_world: bool = True):
    """Register a read-only tool with the annotations connector review requires.

    ``structured_output=False`` keeps the wire format to a single serialized
    text block; FastMCP would otherwise send ``structuredContent`` alongside it
    and double every payload.
    """
    return mcp.tool(
        annotations=ToolAnnotations(
            title=title, readOnlyHint=True, openWorldHint=open_world
        ),
        structured_output=False,
    )


# --------------------------------------------------------------------------- #
# System
# --------------------------------------------------------------------------- #
@read_tool("Server health", open_world=False)
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


@mcp.resource("polymarket://data-model", mime_type="text/markdown")
def data_model() -> str:
    """Polymarket's entity hierarchy (tag/series -> event -> market -> outcome
    token), a glossary of the fields the tools return, and a worked example of
    resolving a search hit down to an order book."""
    return files("polygate_connector").joinpath("data_model.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# Market data
# --------------------------------------------------------------------------- #
@read_tool("List markets")
async def list_markets(
    active: bool = True,
    closed: bool = False,
    tag_id: int | None = None,
    slug: str | None = None,
    limit: int = 10,
    offset: int = 0,
    order: str | None = None,
    ascending: bool | None = None,
    verbosity: Verbosity = "minimal",
) -> dict[str, Any]:
    """List markets (Gamma). Pass `slug` to fetch one market by its slug.

    Each market carries a `conditionId` (the key for `get_market`/`get_holders`)
    and index-aligned `outcomes` and `outcomePrices` (each price is the implied
    probability). Sort with `order` (e.g. 'volume24hr', 'liquidity') plus
    `ascending`. Results arrive as a page of rows with `next_offset` when more
    exist; `limit` is capped at 100 server-side. At the default `verbosity`
    ("minimal") rows form a markdown table; "compact" returns projected
    objects including `clobTokenIds`; "full" returns the raw upstream objects.
    """
    table = MARKET_COLUMNS if verbosity == "minimal" else None
    return await _run_tool(
        "list_markets",
        _require_service().list_markets(
            active=active,
            closed=closed,
            tag_id=tag_id,
            slug=slug,
            limit=limit,
            offset=offset,
            order=order,
            ascending=ascending,
            verbosity=verbosity,
        ),
        table=table,
    )


@read_tool("Get market")
async def get_market(condition_id: str, verbosity: Verbosity = "compact") -> dict[str, Any]:
    """Fetch a single market by its `conditionId` (0x...), as one object.

    Returns the market object (not a list); a bad id yields a `not_found`
    error. The object carries the resolution and status fields - `question`,
    `endDate`, `active`/`closed`/`acceptingOrders`, `outcomes` with
    `outcomePrices` - plus the `clobTokenIds` that key the order-book and
    price tools. `verbosity="full"` returns every upstream field, including
    `description` and `resolutionSource` (the exact resolution criteria).
    """
    return await _run_tool(
        "get_market", _require_service().get_market(condition_id, verbosity=verbosity)
    )


@read_tool("List events")
async def list_events(
    active: bool = True,
    closed: bool = False,
    tag_id: int | None = None,
    series_id: int | None = None,
    limit: int = 10,
    offset: int = 0,
    order: str | None = None,
    verbosity: Verbosity = "minimal",
) -> dict[str, Any]:
    """List events (each event groups one or more markets).

    `tag_id` drills into a category; `series_id` drills into a series (a
    recurring/multi-part group - each Fed decision, a monthly BTC strike
    ladder, a tournament's fixtures). Each row carries a `market_count` and its
    top markets by liquidity; `get_event` returns an event's complete market
    list. Results arrive as a page of rows with `next_offset` when more exist;
    `limit` is capped at 100 server-side.
    """
    return await _run_tool(
        "list_events",
        _require_service().list_events(
            active=active,
            closed=closed,
            tag_id=tag_id,
            series_id=series_id,
            limit=limit,
            offset=offset,
            order=order,
            verbosity=verbosity,
        ),
    )


@read_tool("Get event")
async def get_event(key: str, verbosity: Verbosity = "compact") -> dict[str, Any]:
    """Fetch a single event (with its complete nested markets) by slug or event id.

    An event is a 'market page' grouping one or more atomic markets. Resolves a
    slug or id from `search` or `list_events` into the full object; its
    `seriesSlug`/`gameId` fields identify the related sibling events.
    """
    return await _run_tool(
        "get_event", _require_service().get_event(key, verbosity=verbosity)
    )


@read_tool("List series")
async def list_series(
    limit: int = 20, offset: int = 0, verbosity: Verbosity = "minimal"
) -> dict[str, Any]:
    """List series - Polymarket's recurring/multi-part groupings of events.

    Examples: `fomc` (each Fed decision), `cpi`, `btc-multi-strikes-weekly`,
    `nyc-daily-weather`, a sports league or tournament. A lightweight catalog:
    each entry carries an `event_count` instead of its events. Drill into a
    series with `list_events(series_id=...)` or flatten it with
    `collect_markets(series_id=...)`. At the default `verbosity` rows form a
    markdown table.
    """
    table = SERIES_COLUMNS if verbosity == "minimal" else None
    return await _run_tool(
        "list_series",
        _require_service().list_series(limit=limit, offset=offset, verbosity=verbosity),
        table=table,
    )


@read_tool("Collect markets in scope")
async def collect_markets(
    series_id: int | None = None,
    tag_id: int | None = None,
    event: str | None = None,
    group_by: str | None = None,
    active: bool = True,
    closed: bool = False,
    verbosity: Verbosity = "minimal",
) -> dict[str, Any]:
    """Flatten every atomic market under one grouping node into a single flat list.

    Polymarket splits related markets across separate sibling events, so
    `search` and a single event show only a fragment; this gathers the whole
    scope. Pass EXACTLY ONE of:
    - `series_id` - every market in that series' events.
    - `tag_id` - every market in that category's events.
    - `event` (slug or id) - that event's markets; with `group_by="gameId"` the
      event expands to the sibling events sharing its `gameId` (a sports
      fixture's moneyline, spread, totals, ...). `group_by` names any event
      attribute - no key is hardcoded.

    Rows are the flat markets, each tagged with its parent `event_id`/
    `event_title`/`event_slug` and carrying its `conditionId`; `context`
    echoes the resolved scope and event count. The scan runs to completion
    and errors (with narrowing guidance) if the scope is too broad or too
    slow to gather - never silently partial.
    """
    return await _run_tool(
        "collect_markets",
        _require_service().collect_markets(
            series_id=series_id,
            tag_id=tag_id,
            event=event,
            group_by=group_by,
            active=active,
            closed=closed,
            verbosity=verbosity,
        ),
    )


@read_tool("List categories")
async def list_tags(verbosity: Verbosity = "minimal") -> dict[str, Any]:
    """List the category tags markets can be filtered by (id, label, slug)."""
    return await _run_tool("list_tags", _require_service().list_tags(verbosity=verbosity))


@read_tool("Get order book")
async def get_order_book(token_id: str, full: bool = False) -> dict[str, Any]:
    """CLOB order book for an outcome token (`clobTokenId`), summarized.

    Returns a `summary` (`best_bid`/`best_ask` with sizes, `midpoint`,
    `spread`), the top 5 levels per side best-first with cumulative size
    (`bids_top`/`asks_top`), and per-side `depth` totals. `full=True` includes
    the complete raw ladder as well.
    """
    return await _run_tool(
        "get_order_book", _require_service().order_book(token_id, full=full)
    )


@read_tool("Get last trade price")
async def get_last_trade_price(token_id: str) -> dict[str, Any]:
    """Last traded price for an outcome token - live CLOB, more current than Gamma's cached `bestBid`/`bestAsk`."""
    return await _run_tool(
        "get_last_trade_price", _require_service().last_trade_price(token_id)
    )


@read_tool("Get price history")
async def get_prices_history(
    token_id: str,
    interval: str | None = None,
    start_ts: int | None = None,
    end_ts: int | None = None,
    fidelity: int | None = None,
    raw: bool = False,
) -> dict[str, Any]:
    """Historical price series for an outcome token, summarized by default.

    Provide either `interval` (e.g. '1h', '6h', '1d', '1w', 'max') or a
    `start_ts`/`end_ts` Unix-seconds window; `fidelity` is the resolution in
    minutes. With none of these the window defaults to one week at hourly
    resolution, and the applied `interval` is echoed back. The default result
    is a window `summary` (first/last/min/max/change/n_points); `raw=True`
    returns every point instead.
    """
    return await _run_tool(
        "get_prices_history",
        _require_service().prices_history(
            token_id,
            interval=interval,
            start_ts=start_ts,
            end_ts=end_ts,
            fidelity=fidelity,
            raw=raw,
        ),
    )


# --------------------------------------------------------------------------- #
# Research
# --------------------------------------------------------------------------- #
@read_tool("Search Polymarket")
async def search(
    q: str,
    limit_per_type: int | None = None,
    page: int | None = None,
    events_status: str | None = None,
    verbosity: Verbosity = "minimal",
) -> dict[str, Any]:
    """Full-text search over Polymarket events.

    Returns matching events as rows; each carries a `market_count` and its top
    markets by liquidity, and `get_event(<slug>)` returns an event's complete
    market list. Polymarket splits one topic across sibling events (a game's
    moneyline, spreads, and exact-score are separate events), so
    `collect_markets(event=<slug>, group_by="gameId")` gathers every market
    for a fixture. `limit_per_type` bounds the number of events (capped at 100
    server-side); `events_status` filters e.g. 'active' or 'resolved'; a
    `next_page` field appears when more results exist. At the default
    `verbosity` rows form a markdown table.
    """
    table = SEARCH_COLUMNS if verbosity == "minimal" else None
    return await _run_tool(
        "search",
        _require_service().search(
            q,
            limit_per_type=limit_per_type,
            page=page,
            events_status=events_status,
            verbosity=verbosity,
        ),
        table=table,
    )


@read_tool("Get event comments")
async def get_comments(
    event_id: int,
    limit: int = 20,
    offset: int = 0,
    order: str | None = None,
    ascending: bool | None = None,
    verbosity: Verbosity = "minimal",
) -> dict[str, Any]:
    """Public comments on an event (by its numeric event id). Unverified user content.

    Each row carries the comment `body`, `createdAt`, `author` display name,
    and `reactionCount`. Results arrive as a page of rows with `next_offset`
    when more exist; `limit` is capped at 100 server-side.
    """
    return await _run_tool(
        "get_comments",
        _require_service().comments(
            event_id,
            limit=limit,
            offset=offset,
            order=order,
            ascending=ascending,
            verbosity=verbosity,
        ),
    )


@read_tool("Get top holders")
async def get_holders(
    condition_id: str, limit: int = 20, verbosity: Verbosity = "minimal"
) -> dict[str, Any]:
    """Top holders for a market (`conditionId`), one row per holder.

    Each row carries the holder's display name, `amount` (shares), and
    `outcomeIndex` (which side of the market the shares are on, aligned with
    the market's `outcomes` array). `limit` bounds holders per outcome and is
    capped at 100 server-side. At the default `verbosity` rows form a markdown
    table.
    """
    table = HOLDER_COLUMNS if verbosity == "minimal" else None
    return await _run_tool(
        "get_holders",
        _require_service().holders(condition_id, limit=limit, verbosity=verbosity),
        table=table,
    )


def run() -> None:
    """Console-script entrypoint: start the MCP server over stdio."""
    mcp.run()


if __name__ == "__main__":
    run()
