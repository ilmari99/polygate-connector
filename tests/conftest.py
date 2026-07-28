"""Shared pytest fixtures.

Tests run fully offline against the default Polymarket host URLs (respx
intercepts at the httpx transport, so no request leaves the process). Any
host overrides from the developer's environment are cleared so assertions
against the default URLs are deterministic.
"""

from __future__ import annotations

import os

import pytest

for _var in ("GAMMA_HOST", "CLOB_HOST", "DATA_HOST", "PUBLIC_HOST"):
    os.environ.pop(_var, None)

from polygate_connector.config import get_settings  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
