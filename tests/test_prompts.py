"""The instructions and resource must describe data, not steer the model.

The connector review scans for behavioural instructions in tool descriptions
and server instructions; everything shipped to the host has to read as a
description of what the data means.
"""

from __future__ import annotations

import re

from polygate_connector import mcp_server

BEHAVIOURAL = re.compile(
    r"you should|you must|always remember|keep a memory|confirm with the user",
    re.IGNORECASE,
)


def test_instructions_are_short_and_descriptive():
    text = mcp_server.INSTRUCTIONS
    assert len(text) < 2500
    assert text.startswith("Polymarket prediction-market data")
    assert "Read-only" in text
    assert not BEHAVIOURAL.search(text)


async def test_data_model_resource_is_listed_and_small():
    resources = await mcp_server.mcp.list_resources()
    assert [str(r.uri) for r in resources] == ["polymarket://data-model"]
    content = mcp_server.data_model()
    assert len(content) < 4000
    assert "conditionId" in content and "clobTokenId" in content
    assert not BEHAVIOURAL.search(content)


async def test_tool_docstrings_have_no_behavioural_language():
    # "last traded price" is the name of a metric; "trading"/"place an order"
    # style framing is what the review scan flags.
    for tool in await mcp_server.mcp.list_tools():
        assert not BEHAVIOURAL.search(tool.description or ""), tool.name
        for banned in ("trading", "place an order", "you trade"):
            assert banned not in (tool.description or "").lower(), (
                f"{tool.name}: {banned!r}"
            )
