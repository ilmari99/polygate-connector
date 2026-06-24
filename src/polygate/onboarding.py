"""First-run onboarding shared by server startup and the local ``/setup`` page.

Turning a freshly supplied wallet (``PRIVATE_KEY`` + ``FUNDER_ADDRESS``) into a
fully configured, trade-ready service is identical whether the wallet arrives via
``.env`` at boot or via the local setup page at runtime: derive the CLOB API
credentials, detect the order signature type, and persist both to ``.env``. This
module holds that host-agnostic routine so PolyGate stays a general gateway and
the two entry points cannot drift apart.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from pydantic import SecretStr

from .config import Settings
from .core.env_file import find_env_path, upsert_env
from .core.logging import log
from .core.sigtype import detect_signature_type
from .services.credentials import derive_clob_credentials, store_clob_credentials


async def ensure_clob_credentials(settings: Settings) -> None:
    """Derive and persist CLOB API credentials when missing.

    The L2 credentials are deterministically derivable from the wallet key, so the
    server obtains and persists them automatically. They are written back to
    ``.env`` for next time.
    """
    if settings.has_clob_creds:
        return
    log.info("CLOB credentials missing; deriving from wallet key ...")
    try:
        creds = await asyncio.to_thread(derive_clob_credentials, settings)
    except Exception as exc:  # noqa: BLE001
        raise RuntimeError(
            f"Failed to derive CLOB credentials from the wallet key: {exc}. Check "
            "connectivity and PRIVATE_KEY, then try again."
        ) from exc
    path = store_clob_credentials(creds)
    settings.clob_api_key = SecretStr(creds["CLOB_API_KEY"])
    settings.clob_secret = SecretStr(creds["CLOB_SECRET"])
    settings.clob_passphrase = SecretStr(creds["CLOB_PASSPHRASE"])
    log.info("CLOB credentials derived and saved to %s.", path)


async def ensure_signature_type(settings: Settings) -> None:
    """Detect and persist the CLOB signature type when unset.

    The correct type is the account model whose maker holds your funds; it is
    detected by probing each type's collateral balance and saved back to ``.env``.
    If no type shows a balance (e.g. an unfunded account) the server warns and
    proceeds with the default, leaving ``.env`` untouched so detection retries on
    the next start once the account is funded.
    """
    if settings.signature_type is not None:
        return
    try:
        detected = await asyncio.to_thread(detect_signature_type, settings)
    except Exception as exc:  # noqa: BLE001 - detection must never block startup
        log.warning("Signature-type detection failed (%s); using default.", exc)
        return
    if detected is None:
        log.warning(
            "No funds found for any signature type; assuming SIGNATURE_TYPE=%d. "
            "Fund the account and restart to auto-detect, or set SIGNATURE_TYPE "
            "in .env to override.",
            settings.resolved_signature_type,
        )
        return
    path = find_env_path()
    upsert_env(path, {"SIGNATURE_TYPE": str(detected)})
    settings.signature_type = detected
    log.info("Detected SIGNATURE_TYPE=%d and saved to %s.", detected, path)


async def complete_onboarding(settings: Settings) -> None:
    """Run the automatic first-start setup for a configured wallet.

    No-op in dry-run mode (nothing is ever signed) and when no wallet is present
    (the server simply waits for one via ``/setup``). Otherwise it derives the
    CLOB credentials and detects the signature type, persisting both to ``.env``.
    """
    if settings.dry_run or not settings.has_wallet:
        return
    await ensure_clob_credentials(settings)
    await ensure_signature_type(settings)


def save_wallet(settings: Settings, private_key: str, funder_address: str) -> Path:
    """Persist a user-supplied wallet to ``.env`` and update live settings.

    Writes ``PRIVATE_KEY`` and ``FUNDER_ADDRESS`` (chmod 600 via
    :func:`upsert_env`) and mirrors them onto the in-memory settings so the
    running process can finish onboarding without a restart. Any previously
    derived, wallet-specific material (CLOB credentials and signature type) is
    cleared from both ``.env`` and settings so reconfiguring with a new wallet
    re-derives them cleanly. Returns the ``.env`` path that was written.
    """
    path = find_env_path()
    upsert_env(
        path,
        {"PRIVATE_KEY": private_key, "FUNDER_ADDRESS": funder_address},
        remove=("CLOB_API_KEY", "CLOB_SECRET", "CLOB_PASSPHRASE", "SIGNATURE_TYPE"),
    )
    settings.private_key = SecretStr(private_key)
    settings.funder_address = funder_address
    settings.clob_api_key = None
    settings.clob_secret = None
    settings.clob_passphrase = None
    settings.signature_type = None
    return path
