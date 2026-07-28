"""Every tool must carry the annotations connector review checks for."""

from __future__ import annotations

from polygate_connector.mcp_server import mcp


async def test_every_tool_is_annotated_read_only():
    tools = await mcp.list_tools()
    assert len(tools) == 14
    for tool in tools:
        assert tool.annotations is not None, tool.name
        assert tool.annotations.title, tool.name
        assert tool.annotations.readOnlyHint is True, tool.name


async def test_tool_names_fit_the_length_limit():
    for tool in await mcp.list_tools():
        assert len(tool.name) <= 64, tool.name


async def test_tools_reaching_upstream_apis_are_open_world():
    for tool in await mcp.list_tools():
        expected = tool.name != "health"
        assert tool.annotations.openWorldHint is expected, tool.name
