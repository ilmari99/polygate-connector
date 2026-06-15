"""Derive CLOB API credentials from the wallet private key and store them.

These helpers back the server's first-start credential setup: it derives the L2
credentials automatically when they are missing. They perform L1 (private-key)
authentication against the CLOB to obtain the L2 API key / secret / passphrase
used to authenticate trading requests, and persist them to ``.env`` (chmod 600)
without ever printing them.
"""

from __future__ import annotations

from pathlib import Path

from py_clob_client_v2.client import ClobClient

from ..config import Settings
from ..constants import CHAIN_ID, SIGNATURE_TYPE
from ..core.env_file import find_env_path, upsert_env
from ..core.errors import ConfigurationError


def derive_clob_credentials(settings: Settings) -> dict[str, str]:
    """Perform L1 (private-key) auth and return CLOB creds as an env mapping.

    Raises :class:`ConfigurationError` if the wallet is not configured.
    """
    if not settings.has_wallet:
        raise ConfigurationError(
            "PRIVATE_KEY and FUNDER_ADDRESS must be set in .env first "
            "(reveal your key on polymarket.com under Settings -> Account -> Private Key)."
        )
    client = ClobClient(
        host=settings.clob_host,
        chain_id=CHAIN_ID,
        key=settings.private_key.get_secret_value(),
        signature_type=SIGNATURE_TYPE,
        funder=settings.funder_address,
    )
    creds = client.create_or_derive_api_key()
    return {
        "CLOB_API_KEY": creds.api_key,
        "CLOB_SECRET": creds.api_secret,
        "CLOB_PASSPHRASE": creds.api_passphrase,
    }


def store_clob_credentials(creds: dict[str, str]) -> Path:
    """Persist derived CLOB creds into ``.env`` (chmod 600). Returns the path."""
    env_path = find_env_path()
    upsert_env(env_path, creds)
    return env_path
