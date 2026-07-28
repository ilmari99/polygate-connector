"""Common response wrappers shared by every tool."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResponseEnvelope(BaseModel, Generic[T]):
    """Wraps a detail response with the time it was fetched and its source.

    The ``fetched_at`` timestamp lets an agent reason about staleness, which
    matters because the agent (not the platform) drives the polling cadence.
    """

    data: T
    fetched_at: datetime = Field(default_factory=_utcnow)
    source: str = Field(description="Which upstream produced the data, e.g. 'gamma'.")

    @classmethod
    def of(cls, data: T, source: str) -> "ResponseEnvelope[T]":
        return cls(data=data, source=source, fetched_at=_utcnow())


class ListPage(BaseModel):
    """One page of a list tool's results, with the metadata needed to page on.

    ``rows`` is a list of projected row dicts (or, on the wire for some tools
    in minimal verbosity, a rendered markdown table string). ``next_offset`` /
    ``next_page`` are present only when another page exists. ``truncated``
    flags a server-side reduction ONLY (a clamped limit or a size cap) - a
    merely-full page is signalled by ``next_offset``, not ``truncated``.
    ``context`` carries small tool-specific metadata (e.g. the resolved scope
    of a collect).
    """

    rows: Any
    returned: int
    next_offset: int | None = None
    next_page: int | None = None
    truncated: bool = False
    notice: str | None = None
    context: dict[str, Any] | None = None
    fetched_at: datetime = Field(default_factory=_utcnow)
    source: str = Field(description="Which upstream produced the data, e.g. 'gamma'.")

    @classmethod
    def of(
        cls,
        rows: list[Any],
        source: str,
        *,
        next_offset: int | None = None,
        next_page: int | None = None,
        truncated: bool = False,
        notice: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> "ListPage":
        return cls(
            rows=rows,
            returned=len(rows) if isinstance(rows, list) else 0,
            next_offset=next_offset,
            next_page=next_page,
            truncated=truncated,
            notice=notice,
            context=context,
            fetched_at=_utcnow(),
            source=source,
        )
