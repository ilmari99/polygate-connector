"""Static network parameters for Polymarket (Polygon mainnet)."""

from __future__ import annotations

# --- Chain ---
CHAIN_ID = 137  # Polygon mainnet

# --- API hosts ---
GAMMA_HOST = "https://gamma-api.polymarket.com"
CLOB_HOST = "https://clob.polymarket.com"
DATA_HOST = "https://data-api.polymarket.com"

# --- CLOB order signature type ---
# 3 = EIP-1271 "deposit wallet": Polymarket's current account model. Orders are
# signed with your EOA key and validated on-chain by the deposit-wallet contract
# via EIP-1271. This is the only flow this platform supports.
SIGNATURE_TYPE = 3
