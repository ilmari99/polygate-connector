"""Guards against any trading/wallet surface re-entering the package.

The Anthropic connector policy requires write tools to be absent from the
server, not disabled at runtime. These tests fail the suite the moment a
forbidden symbol, dependency, or tool reappears anywhere under ``src/``.
"""

from __future__ import annotations

import re
from pathlib import Path

from polygate_connector import mcp_server

SRC = Path(__file__).resolve().parent.parent / "src" / "polygate_connector"

FORBIDDEN = re.compile(
    r"place_order|cancel_order|PRIVATE_KEY|FUNDER_ADDRESS|DRY_RUN"
    r"|py_clob_client|eth_account",
    re.IGNORECASE,
)

EXPECTED_TOOLS = {
    "health",
    "search",
    "list_markets",
    "get_market",
    "list_events",
    "get_event",
    "list_series",
    "list_tags",
    "collect_markets",
    "get_order_book",
    "get_last_trade_price",
    "get_prices_history",
    "get_comments",
    "get_holders",
}


def test_no_forbidden_symbols_in_source():
    offenders = []
    for path in SRC.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), start=1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(SRC.parent.parent)}:{lineno}: {line.strip()}")
    assert not offenders, "trading/wallet surface reintroduced:\n" + "\n".join(offenders)


def test_instructions_have_no_trading_language():
    assert not FORBIDDEN.search(mcp_server.INSTRUCTIONS)


async def test_exactly_the_fourteen_read_only_tools():
    tools = await mcp_server.mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOLS
