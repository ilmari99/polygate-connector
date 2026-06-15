"""Derive CLOB API credentials from the wallet private key and store them.

Run once after creating/funding the wallet:

    derive-creds

This performs L1 (private-key) authentication against the CLOB and obtains the
L2 API key / secret / passphrase used to authenticate trading requests. The
credentials are written to ``.env`` (chmod 600) and never printed.
"""

from __future__ import annotations

import sys

from py_clob_client_v2.client import ClobClient

from ..config import get_settings
from ..constants import CHAIN_ID, SIGNATURE_TYPE
from .env_file import find_env_path, upsert_env


def main() -> int:
    settings = get_settings()
    if not settings.has_wallet:
        print(
            "ERROR: PRIVATE_KEY and FUNDER_ADDRESS must be set in .env first "
            "(run scripts/generate_wallet.py).",
            file=sys.stderr,
        )
        return 1

    client = ClobClient(
        host=settings.clob_host,
        chain_id=CHAIN_ID,
        key=settings.private_key.get_secret_value(),
        signature_type=SIGNATURE_TYPE,
        funder=settings.funder_address,
    )

    print("Deriving CLOB API credentials via L1 (private-key) auth ...")
    try:
        creds = client.create_or_derive_api_key()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: failed to derive credentials: {exc}", file=sys.stderr)
        return 1

    env_path = find_env_path()
    upsert_env(
        env_path,
        {
            "CLOB_API_KEY": creds.api_key,
            "CLOB_SECRET": creds.api_secret,
            "CLOB_PASSPHRASE": creds.api_passphrase,
        },
    )
    print(f"Success. CLOB credentials written to {env_path} (chmod 600).")
    print("They were NOT printed here. You can now place authenticated requests.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
