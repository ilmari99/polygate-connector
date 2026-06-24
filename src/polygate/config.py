"""Application configuration loaded from environment / `.env`.

Secrets are stored as :class:`~pydantic.SecretStr` so they are never accidentally
printed in logs or error messages. Use :meth:`Settings.public_summary` for any
user-facing description of the active configuration.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import (
    CHAIN_ID,
    CLOB_HOST,
    DATA_HOST,
    DEFAULT_SIGNATURE_TYPE,
    GAMMA_HOST,
)
from .core.env_file import find_env_path


def _env_file() -> str:
    """Where to load ``.env`` from.

    Delegates to :func:`find_env_path` so the server reads its configuration from
    exactly the same file that setup writes to. Resolving this in two places used
    to drift (write to ``~/.polygate/.env`` but read a cwd-relative ``.env``),
    which silently dropped the wallet on restart.
    """
    return str(find_env_path())


class Settings(BaseSettings):
    """Runtime settings for the trading platform."""

    model_config = SettingsConfigDict(
        env_file=_env_file(),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Account ---
    # PRIVATE_KEY signs orders (your EOA). FUNDER_ADDRESS is your Polymarket
    # proxy-wallet address (Settings -> Profile -> Address); it holds the funds
    # and is the order maker. The two are different addresses.
    private_key: SecretStr | None = Field(default=None)
    funder_address: str | None = Field(default=None)

    # --- CLOB API credentials (derived from the private key automatically at startup) ---
    clob_api_key: SecretStr | None = Field(default=None)
    clob_secret: SecretStr | None = Field(default=None)
    clob_passphrase: SecretStr | None = Field(default=None)

    # --- CLOB order signature type ---
    # Which account model the funder uses (0 EOA, 1 proxy, 2 safe, 3 deposit
    # wallet). Detected automatically at startup from which maker holds your funds
    # and saved to .env; leave unset to auto-detect.
    signature_type: int | None = Field(default=None)

    # --- Platform REST API protection ---
    platform_api_key: SecretStr | None = Field(default=None)

    # --- Developer dry-run switch (default off; orders are real) ---
    # When true, orders are simulated and never signed or sent. Used by the test
    # suite and local development; not part of normal operation.
    dry_run: bool = Field(default=False)

    # --- Polymarket hosts (overridable for testing) ---
    gamma_host: str = Field(default=GAMMA_HOST)
    clob_host: str = Field(default=CLOB_HOST)
    data_host: str = Field(default=DATA_HOST)

    # --- Server bind ---
    host: str = Field(default="127.0.0.1")
    port: int = Field(default=8000)
    log_level: str = Field(default="INFO")

    # --- Outbound HTTP behaviour ---
    http_timeout_seconds: float = Field(default=15.0)
    http_max_retries: int = Field(default=3)

    @field_validator("funder_address")
    @classmethod
    def _checksum_optional_address(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return None
        if not (value.startswith("0x") and len(value) == 42):
            raise ValueError("FUNDER_ADDRESS must be a 0x-prefixed 42-char address")
        return value

    @field_validator("signature_type")
    @classmethod
    def _valid_signature_type(cls, value: int | None) -> int | None:
        if value is not None and value not in (0, 1, 2, 3):
            raise ValueError("SIGNATURE_TYPE must be one of 0, 1, 2, 3")
        return value

    # --- Derived helpers ---
    @property
    def has_wallet(self) -> bool:
        return self.private_key is not None and self.funder_address is not None

    @property
    def resolved_signature_type(self) -> int:
        """Signature type to use, falling back to the default when unset."""
        return (
            self.signature_type
            if self.signature_type is not None
            else DEFAULT_SIGNATURE_TYPE
        )

    @property
    def has_clob_creds(self) -> bool:
        return all(
            v is not None
            for v in (self.clob_api_key, self.clob_secret, self.clob_passphrase)
        )

    @property
    def can_trade_live(self) -> bool:
        """True only when everything needed for real order placement is present."""
        return not self.dry_run and self.has_wallet and self.has_clob_creds

    def public_summary(self) -> dict:
        """A secret-free description of the active configuration."""
        return {
            "mode": "dry-run" if self.dry_run else "LIVE",
            "chain_id": CHAIN_ID,
            "signature_type": self.resolved_signature_type,
            "wallet_address": self.funder_address,
            "wallet_configured": self.has_wallet,
            "clob_creds_configured": self.has_clob_creds,
            "platform_auth_enabled": self.platform_api_key is not None,
            "hosts": {
                "gamma": self.gamma_host,
                "clob": self.clob_host,
                "data": self.data_host,
            },
        }


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
