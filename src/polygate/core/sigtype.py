"""Detect the Polymarket CLOB signature type from the account's funded balance.

The signature type tells the CLOB how an order's maker (``FUNDER_ADDRESS``)
relates to the signer (``PRIVATE_KEY`` EOA): ``0`` EOA, ``1`` POLY_PROXY
(email/Google sign-up), ``2`` POLY_GNOSIS_SAFE (browser wallet), ``3`` POLY_1271
(deposit wallet). Each type maps to a *different* maker address, and the CLOB
tracks collateral per maker, so the correct type is simply the one whose maker
actually holds USDC. We probe each type's collateral balance and pick the funded
one. If no type shows a balance (e.g. an unfunded account) the caller is expected
to fall back to the default and warn.
"""

from __future__ import annotations

from py_clob_client_v2.client import ClobClient
from py_clob_client_v2.clob_types import ApiCreds, AssetType, BalanceAllowanceParams

from ..config import Settings
from ..constants import CHAIN_ID
from .logging import log

# Signature types to probe, most common first.
_CANDIDATE_TYPES: tuple[int, ...] = (1, 3, 2, 0)


def _collateral_balance(settings: Settings, signature_type: int) -> int:
    """Return the collateral balance (in USDC base units) for one signature type.

    Returns ``0`` on any error so a single failed probe never aborts detection.
    """
    creds = ApiCreds(
        api_key=settings.clob_api_key.get_secret_value(),
        api_secret=settings.clob_secret.get_secret_value(),
        api_passphrase=settings.clob_passphrase.get_secret_value(),
    )
    client = ClobClient(
        host=settings.clob_host,
        chain_id=CHAIN_ID,
        key=settings.private_key.get_secret_value(),
        creds=creds,
        signature_type=signature_type,
        funder=settings.funder_address,
    )
    params = BalanceAllowanceParams(
        asset_type=AssetType.COLLATERAL,
        token_id=None,
        signature_type=signature_type,
    )
    try:
        result = client.get_balance_allowance(params)
    except Exception as exc:  # noqa: BLE001 - probe failures must not be fatal
        log.debug("Balance probe for signature type %d failed: %s", signature_type, exc)
        return 0
    raw = result.get("balance") if isinstance(result, dict) else None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def detect_signature_type(settings: Settings) -> int | None:
    """Probe each signature type and return the one whose maker holds funds.

    Returns the funded signature type (``0``/``1``/``2``/``3``), or ``None`` when
    no probed type shows a positive collateral balance. Requires CLOB credentials
    and a configured wallet; the balance read is a side-effect-free GET.
    """
    for signature_type in _CANDIDATE_TYPES:
        balance = _collateral_balance(settings, signature_type)
        if balance > 0:
            log.info(
                "Signature type %d holds %.6f USDC; selecting it.",
                signature_type,
                balance / 1_000_000,
            )
            return signature_type
    return None
