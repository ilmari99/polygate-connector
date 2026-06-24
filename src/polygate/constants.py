"""Static network parameters for Polymarket (Polygon mainnet)."""

from __future__ import annotations

# --- Chain ---
CHAIN_ID = 137  # Polygon mainnet

# --- API hosts ---
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"

# --- CLOB order signature type ---
# How the order maker (FUNDER_ADDRESS) relates to the signer (PRIVATE_KEY):
#   0 = EOA, 1 = POLY_PROXY (email/Google sign-up), 2 = POLY_GNOSIS_SAFE
#   (browser wallet), 3 = POLY_1271 (deposit wallet).
# The correct value is detected automatically at startup from which maker holds
# your funds (see core.sigtype) and saved to .env. This is only the fallback used
# when detection finds no funds and no value is configured.
DEFAULT_SIGNATURE_TYPE = 1
