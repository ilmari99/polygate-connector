"""Shared async HTTP helper with retries for Polymarket's public REST APIs."""

from __future__ import annotations

from typing import Any

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from .. import __version__
from ..core.errors import UpstreamError
from ..core.logging import log

# Exceptions worth retrying: transient network issues and 5xx responses.
_RETRYABLE = (httpx.TransportError, httpx.HTTPStatusError)


class HttpClient:
    """Thin wrapper around :class:`httpx.AsyncClient` with retry + error mapping."""

    def __init__(self, *, timeout: float = 15.0, max_retries: int = 3):
        self._client = httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": f"polygate/{__version__}"},
            follow_redirects=True,
        )
        self._max_retries = max(1, max_retries)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_json(
        self, url: str, *, params: dict[str, Any] | None = None, source: str = "upstream"
    ) -> Any:
        """GET ``url`` and return parsed JSON, retrying transient failures."""

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.3, max=4),
            retry=retry_if_exception_type(_RETRYABLE),
        )
        async def _do() -> Any:
            resp = await self._client.get(url, params=params)
            # Only retry on server errors; 4xx are surfaced immediately below.
            if resp.status_code >= 500:
                resp.raise_for_status()
            return resp

        try:
            resp = await _do()
        except httpx.HTTPStatusError as exc:
            raise UpstreamError(
                f"{source} returned {exc.response.status_code}", code=f"{source}_error"
            ) from exc
        except httpx.HTTPError as exc:
            raise UpstreamError(f"{source} request failed: {exc}", code=f"{source}_error") from exc

        if resp.status_code >= 400:
            log.warning("%s %s -> %s", source, url, resp.status_code)
            raise UpstreamError(
                f"{source} returned {resp.status_code}: {resp.text[:200]}",
                code=f"{source}_error",
                status_code=502 if resp.status_code >= 500 else resp.status_code,
            )
        try:
            return resp.json()
        except ValueError as exc:
            raise UpstreamError(f"{source} returned non-JSON response", code=f"{source}_error") from exc
