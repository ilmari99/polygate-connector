"""Static network parameters for Polymarket (Polygon mainnet)."""

from __future__ import annotations

# --- Chain ---
CHAIN_ID = 137  # Polygon mainnet

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

# --- CLOB order signature type ---
# How the order maker (FUNDER_ADDRESS) relates to the signer (PRIVATE_KEY):
#   0 = EOA, 1 = POLY_PROXY (email/Google sign-up), 2 = POLY_GNOSIS_SAFE
#   (browser wallet), 3 = POLY_1271 (deposit wallet).
# The correct value is detected automatically at startup from which maker holds
# your funds (see core.sigtype) and saved to .env. This is only the fallback used
# when detection finds no funds and no value is configured.
DEFAULT_SIGNATURE_TYPE = 1
