"""A configured local MCP command remains executable without package downloads."""

import sys

import pytest

from xagent.core.tools.adapters.vibe.mcp_adapter import load_mcp_tools_as_agent_tools


@pytest.mark.asyncio
async def test_local_stdio_tool_executes_through_agent_adapter(tmp_path):
    server = tmp_path / "arithmetic.py"
    server.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "server = FastMCP('local-arithmetic')\n"
        "@server.tool()\n"
        "def add(left: int, right: int) -> int:\n"
        "    return left + right\n"
        "server.run(transport='stdio')\n",
        encoding="utf-8",
    )
    loaded = await load_mcp_tools_as_agent_tools(
        {
            "local-arithmetic": {
                "transport": "stdio",
                "command": sys.executable,
                "args": [str(server)],
            }
        }
    )
    assert not loaded.failures, loaded.failures
    tool = next(tool for tool in loaded.tools if tool.name.endswith("add"))
    result = await tool.run_json_async({"left": 19, "right": 23})
    assert result["is_error"] is False, result
    assert any(item.get("text") == "42" for item in result["content"]), result
