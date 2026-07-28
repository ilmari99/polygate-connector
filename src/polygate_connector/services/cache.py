"""A small in-process TTL cache for upstream reads.

Research traffic is overwhelmingly repeat queries, and every user of a shared
deployment exits through one IP - caching is both the latency win and what
keeps the server inside Polymarket's rate limits. Only successful responses
are stored; errors always propagate uncached. No external dependency: one
dict, monotonic clock, oldest-first eviction.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import Any

from ..constants import CACHE_TTLS

_MISS = object()


def _now() -> float:
    return time.monotonic()


def ttl_for(path: str) -> float:
    """Seconds a response for this upstream path stays fresh (0 = uncached)."""
    return CACHE_TTLS.get(path, 0.0)


class TTLCache:
    """Per-entry-TTL cache with oldest-first eviction at ``maxsize``."""

    def __init__(self, maxsize: int = 1024):
        self._maxsize = maxsize
        self._entries: OrderedDict[Any, tuple[float, Any]] = OrderedDict()

    def get(self, key: Any) -> Any:
        """Return the cached value, or the :data:`MISS` sentinel."""
        entry = self._entries.get(key)
        if entry is None:
            return _MISS
        expires_at, value = entry
        if _now() >= expires_at:
            del self._entries[key]
            return _MISS
        return value

    def set(self, key: Any, value: Any, ttl: float) -> None:
        if ttl <= 0:
            return
        self._entries[key] = (_now() + ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._maxsize:
            self._entries.popitem(last=False)

    @staticmethod
    def is_miss(value: Any) -> bool:
        return value is _MISS
