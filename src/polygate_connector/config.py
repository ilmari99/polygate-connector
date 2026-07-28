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
    # Max upstream requests in flight at once (shared across all callers).
    upstream_concurrency: int = Field(default=8)

    # --- HTTP serving (serve.py) ---
    # The public hostname the connector is reached at (e.g. mcp.example.com);
    # Host/Origin validation is pinned to it. Required to serve over HTTP.
    public_host: str | None = Field(default=None)
    # Bind address for uvicorn. Loopback by default: only the tunnel (or a
    # container port mapping) should reach the process directly.
    bind_host: str = Field(default="127.0.0.1")
    bind_port: int = Field(default=8765)
    # slowapi rate-limit expressions. Stateless MCP is chatty (initialize +
    # list_tools + every call is a POST), so the per-IP limit leaves headroom
    # for one busy Claude session.
    rate_limit_per_ip: str = Field(default="60/minute")
    rate_limit_global: str = Field(default="600/minute")


@lru_cache
def get_settings() -> Settings:
    """Return a cached :class:`Settings` instance."""
    return Settings()
