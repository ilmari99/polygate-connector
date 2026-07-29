"""Tests for markdown table rendering and the response size cap."""

from __future__ import annotations

from polygate_connector.constants import RESPONSE_MAX_BYTES
from polygate_connector.render import (
    HOLDER_COLUMNS,
    MARKET_COLUMNS,
    enforce_size_cap,
    markdown_table,
    payload_bytes,
)


def _market_row(i: int = 0) -> dict:
    return {
        "question": f"Will thing {i} happen?",
        "outcomes": ["Yes", "No"],
        "outcomePrices": ["0.62", "0.38"],
        "volume24hr": 1000.5,
        "liquidityNum": 500.0,
        "endDate": "2026-12-31T00:00:00Z",
        "conditionId": f"0xabc{i}",
    }


def test_market_table_combines_outcomes_and_prices():
    table = markdown_table([_market_row()], MARKET_COLUMNS)
    lines = table.splitlines()
    assert lines[0].startswith("| question |")
    assert lines[1].startswith("| --- |")
    assert "Yes 0.62 / No 0.38" in lines[2]
    assert "0xabc0" in lines[2]  # the id needed for follow-up calls survives
    assert "2026-12-31 " in lines[2] or "| 2026-12-31 |" in lines[2]  # date only


def test_market_table_shows_book_edge():
    row = {**_market_row(), "bestBid": 0.01, "bestAsk": 0.65}
    table = markdown_table([row], MARKET_COLUMNS)
    assert "bestBid / bestAsk" in table.splitlines()[0]
    assert "0.01 / 0.65" in table.splitlines()[2]
    # A row with no book fields renders an empty cell, not a crash.
    bare = markdown_table([_market_row()], MARKET_COLUMNS)
    assert len(bare.splitlines()) == 3


def test_table_escapes_pipes_and_newlines():
    rows = [{"outcomeIndex": 0, "name": "a|b", "amount": 1.0}]
    table = markdown_table(rows, HOLDER_COLUMNS)
    assert "a\\|b" in table
    rows = [{"outcomeIndex": 0, "name": "a\nb", "amount": 1.0}]
    assert "a b" in markdown_table(rows, HOLDER_COLUMNS)


def test_table_renders_none_as_empty_and_skips_non_dicts():
    table = markdown_table([{"outcomeIndex": None, "amount": 2}, "junk"], HOLDER_COLUMNS)
    assert len(table.splitlines()) == 3  # header + divider + one row
    assert "|  |" in table.splitlines()[2]


def test_size_cap_passthrough_when_small():
    payload = {"rows": [1, 2, 3], "returned": 3}
    assert enforce_size_cap(payload) is payload


def test_size_cap_halves_list_rows_with_notice():
    big_row = {"question": "x" * 2000}
    payload = {"rows": [dict(big_row) for _ in range(200)], "returned": 200}
    out = enforce_size_cap(payload)
    assert payload_bytes(out) <= RESPONSE_MAX_BYTES
    assert out["returned"] == len(out["rows"]) > 0
    assert out["truncated"] is True
    assert "response cap" in out["notice"]


def test_size_cap_trims_table_string_by_lines():
    lines = ["| a | b |", "| --- | --- |"] + [f"| {'x' * 500} | {i} |" for i in range(400)]
    payload = {"rows": "\n".join(lines), "returned": 400}
    out = enforce_size_cap(payload)
    assert payload_bytes(out) <= RESPONSE_MAX_BYTES
    trimmed = out["rows"].splitlines()
    assert trimmed[0] == "| a | b |" and trimmed[1] == "| --- | --- |"  # still a table
    assert out["returned"] == len(trimmed) - 2
    assert out["truncated"] is True


def test_size_cap_replaces_oversized_detail_payload():
    payload = {"data": {"blob": "x" * (RESPONSE_MAX_BYTES + 1)}, "source": "gamma"}
    out = enforce_size_cap(payload)
    assert out["error"] == "response_too_large"
    assert "verbosity" in out["detail"]
