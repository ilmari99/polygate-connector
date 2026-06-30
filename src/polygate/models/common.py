"""Common response wrappers shared by every endpoint."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Generic, TypeVar

from pydantic import BaseModel, Field

T = TypeVar("T")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResponseEnvelope(BaseModel, Generic[T]):
    """Wraps every data response with the time it was fetched and its source.

    The ``fetched_at`` timestamp lets a trading agent reason about staleness,
    which matters because the agent (not the platform) drives the polling
    cadence.
    """

    data: T
    fetched_at: datetime = Field(default_factory=_utcnow)
    source: str = Field(description="Which upstream produced the data, e.g. 'gamma'.")

    @classmethod
    def of(cls, data: T, source: str) -> "ResponseEnvelope[T]":
        return cls(data=data, source=source, fetched_at=_utcnow())
