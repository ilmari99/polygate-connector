"""Shared pytest fixtures.

Tests run fully offline. They set a dummy ``PLATFORM_API_KEY`` and keep
``DRY_RUN=true`` so no signing, broadcasting, or live trading can occur, and they
clear the settings cache so each test sees a clean configuration.
"""

from __future__ import annotations

import os

import pytest

# Configure a safe environment BEFORE importing application modules.
os.environ.setdefault("PLATFORM_API_KEY", "test-platform-key")
os.environ.setdefault("DRY_RUN", "true")
os.environ.setdefault("FUNDER_ADDRESS", "0x000000000000000000000000000000000000dEaD")

from polygate.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def api_key() -> str:
    return "test-platform-key"


@pytest.fixture
def auth_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}
