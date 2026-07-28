"""Static parameters for Polymarket's public read APIs."""

from __future__ import annotations

# --- API hosts ---
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"

# Gamma caps a single ``/markets`` or ``/events`` page at 100 rows regardless of
# the requested ``limit``. The facade transparently pages around this cap so a
# caller asking for more than 100 actually receives them.
GAMMA_PAGE_LIMIT = 100

# The CLOB ``/prices-history`` endpoint rejects a call with no time window
# ("the time component is mandatory"), so a bare ``get_prices_history(token_id)``
# would error. We default to this interval when the caller supplies neither an
# ``interval`` nor a ``start_ts``/``end_ts`` window, and report the applied value
# back so the caller knows the span it received. A wide interval also needs a
# ``fidelity`` (the CLOB enforces a minimum for it), so we pair the default with
# an hourly resolution that both satisfies that floor and keeps the series small.
DEFAULT_PRICES_HISTORY_INTERVAL = "1w"
DEFAULT_PRICES_HISTORY_FIDELITY = 60  # minutes (hourly)

# Safety valve for ``collect_markets``: the most events a single scoped flatten
# will page through before failing loud. Gamma has no server-side filter for
# per-event attributes (e.g. a sports ``gameId``), so gathering a group means
# scanning its whole series/tag and filtering client-side. Real series are small
# (dozens); a scope that blows past this cap is too broad and must be narrowed,
# so we raise rather than silently return an incomplete set.
MARKET_SCAN_MAX_EVENTS = 5000

# Wall-clock budget for one collect_markets call. The scan pages upstream
# sequentially, so a broad scope could otherwise outlive the MCP client's
# request timeout; past the deadline it fails loud with narrowing guidance.
COLLECT_SCAN_DEADLINE_SECONDS = 20.0

# --- List defaults and bounds ---
# Response size is a connector review criterion: list tools default small and
# page, rather than defaulting large. The caller's limit is clamped server-side
# because the requesting model's number cannot be trusted.
DEFAULT_LIST_LIMIT = 10   # list_markets, list_events, search
DEFAULT_WIDE_LIMIT = 20   # list_series, get_holders, get_comments
MAX_LIST_LIMIT = 100

# Hard ceiling on a single serialized tool result. Anything larger is reduced
# (rows dropped, with an explicit notice) before it reaches the client.
RESPONSE_MAX_BYTES = 100_000

# --- Upstream response cache (seconds per API path) ---
# Catalog listings move slowly; live CLOB data moves fast; search results and
# social data sit in between. A path missing here is never cached.
CACHE_TTLS = {
    "/markets": 45.0,
    "/events": 45.0,
    "/series": 45.0,
    "/tags": 45.0,
    "/public-search": 300.0,
    "/book": 15.0,
    "/last-trade-price": 15.0,
    "/prices-history": 15.0,
    "/comments": 120.0,
    "/holders": 120.0,
}
