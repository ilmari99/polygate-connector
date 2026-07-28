"""Application configuration loaded from environment / `.env`.

The server holds no secrets: configuration is limited to the public Polymarket
API hosts, logging, and outbound HTTP behaviour.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from .constants import CLOB_HOST, DATA_HOST, GAMMA_HOST


class Settings(BaseSettings):
    """Runtime settings for the read-only research server."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Polymarket hosts (overridable for testing) ---
    gamma_host: str = Field(default=GAMMA_HOST)
    clob_host: str = Field(default=CLOB_HOST)
    data_host: str = Field(default=DATA_HOST)

    # --- Logging ---
    log_level: str = Field(default="INFO")

    # --- Outbound HTTP behaviour ---
    http_timeout_seconds: float = Field(default=15.0)
    http_max_retries: int = Field(default=3)


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
