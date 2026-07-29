"""Presentation layer: markdown tables for list rows and the response size cap.

JSON key repetition is roughly half the payload of a list response, so the
minimal-verbosity list tools render their rows as a markdown table instead -
same information, about half the tokens. The size cap is the last line of
defence: no serialized tool result may exceed :data:`RESPONSE_MAX_BYTES`, and
any reduction is announced in an explicit ``notice`` rather than applied
silently.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from .constants import RESPONSE_MAX_BYTES

# A table column: (header, getter over the row dict).
Column = tuple[str, Callable[[dict[str, Any]], Any]]


def _get(key: str) -> Callable[[dict[str, Any]], Any]:
    return lambda row: row.get(key)


def _date(key: str) -> Callable[[dict[str, Any]], Any]:
    """Render an ISO timestamp as its date part."""

    def getter(row: dict[str, Any]) -> Any:
        value = row.get(key)
        return value[:10] if isinstance(value, str) and len(value) >= 10 else value

    return getter


def _outcome_prices(row: dict[str, Any]) -> Any:
    """Combine index-aligned outcomes and prices into one readable cell."""
    outcomes, prices = row.get("outcomes"), row.get("outcomePrices")
    if isinstance(outcomes, list) and isinstance(prices, list) and len(outcomes) == len(prices):
        return " / ".join(f"{o} {p}" for o, p in zip(outcomes, prices))
    return row.get("outcomePrices")


def _bid_ask(row: dict[str, Any]) -> Any:
    """The executable book edge; `outcomePrices` alone (a midpoint) misleads
    on wide-spread books, so every market row shows both."""
    bid, ask = row.get("bestBid"), row.get("bestAsk")
    if bid is None and ask is None:
        return None
    return f"{'-' if bid is None else bid} / {'-' if ask is None else ask}"


MARKET_COLUMNS: list[Column] = [
    ("question", _get("question")),
    ("outcomes (price = implied probability)", _outcome_prices),
    ("bestBid / bestAsk", _bid_ask),
    ("volume24hr", _get("volume24hr")),
    ("liquidity", _get("liquidityNum")),
    ("endDate", _date("endDate")),
    ("conditionId", _get("conditionId")),
]

SEARCH_COLUMNS: list[Column] = [
    ("title", _get("title")),
    ("slug", _get("slug")),
    ("markets", _get("market_count")),
    ("volume", _get("volume")),
    ("endDate", _date("endDate")),
]

SERIES_COLUMNS: list[Column] = [
    ("id", _get("id")),
    ("slug", _get("slug")),
    ("title", _get("title")),
    ("recurrence", _get("recurrence")),
    ("events", _get("event_count")),
    ("volume24hr", _get("volume24hr")),
]

HOLDER_COLUMNS: list[Column] = [
    ("outcomeIndex", _get("outcomeIndex")),
    ("holder", lambda r: r.get("name") or r.get("pseudonym")),
    ("amount (shares)", _get("amount")),
]


def _cell(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def markdown_table(rows: list[Any], columns: list[Column]) -> str:
    """Render row dicts as a markdown table; non-dict rows are skipped."""
    headers = [header for header, _ in columns]
    lines = [
        "| " + " | ".join(headers) + " |",
        "| " + " | ".join("---" for _ in headers) + " |",
    ]
    for row in rows:
        if not isinstance(row, dict):
            continue
        lines.append(
            "| " + " | ".join(_cell(getter(row)) for _, getter in columns) + " |"
        )
    return "\n".join(lines)


def payload_bytes(payload: Any) -> int:
    return len(json.dumps(payload, separators=(",", ":"), default=str))


def enforce_size_cap(payload: dict[str, Any]) -> dict[str, Any]:
    """Reduce an oversized payload by shape, never silently.

    List rows are halved until the payload fits; a rendered table loses
    trailing lines (staying a valid table); a detail payload too large to
    reduce is replaced with an actionable error. Every reduction sets an
    explicit ``notice``.
    """
    if payload_bytes(payload) <= RESPONSE_MAX_BYTES:
        return payload
    rows = payload.get("rows")
    if isinstance(rows, list):
        out = dict(payload)
        while rows and payload_bytes(out) > RESPONSE_MAX_BYTES:
            rows = rows[: max(1, len(rows) // 2)] if len(rows) > 1 else []
            out["rows"] = rows
            out["returned"] = len(rows)
        out["truncated"] = True
        out["notice"] = (
            f"result exceeded the {RESPONSE_MAX_BYTES // 1000}KB response cap; "
            f"reduced to {len(rows)} row(s) - page with offset or lower limit"
        )
        return out
    if isinstance(rows, str):
        out = dict(payload)
        lines = rows.splitlines()
        while len(lines) > 2 and payload_bytes(out) > RESPONSE_MAX_BYTES:
            lines = lines[: max(2, len(lines) // 2)]
            out["rows"] = "\n".join(lines)
        returned = max(0, len(lines) - 2)
        out["returned"] = returned
        out["truncated"] = True
        out["notice"] = (
            f"result exceeded the {RESPONSE_MAX_BYTES // 1000}KB response cap; "
            f"reduced to {returned} row(s) - page with offset or lower limit"
        )
        return out
    return {
        "error": "response_too_large",
        "detail": (
            f"result exceeded the {RESPONSE_MAX_BYTES // 1000}KB response cap; "
            "retry with verbosity='compact' or 'minimal', or narrow the request"
        ),
    }
