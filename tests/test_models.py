"""Tests for the response envelope."""

from __future__ import annotations

from datetime import datetime, timezone

from polygate.models.common import ResponseEnvelope


def test_envelope_sets_timestamp_and_source():
    env = ResponseEnvelope.of({"hello": "world"}, source="gamma")
    assert env.data == {"hello": "world"}
    assert env.source == "gamma"
    assert env.fetched_at.tzinfo is not None
    # Timestamp should be very recent and timezone-aware (UTC).
    delta = datetime.now(timezone.utc) - env.fetched_at
    assert 0 <= delta.total_seconds() < 5
