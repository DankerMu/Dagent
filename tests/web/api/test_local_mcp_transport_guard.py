"""Unsupported local MCP transports cannot launch a connection probe."""

import pytest

from xagent.core.tools.adapters.vibe import mcp_adapter
from xagent.web.api.mcp import MCPConnectionTest
from xagent.web.api.mcp import test_mcp_connection as probe_mcp_connection


@pytest.mark.asyncio
async def test_connection_probe_rejects_unsupported_transport_before_network(
    monkeypatch,
):
    async def unexpected_loader(*args, **kwargs):
        raise AssertionError("unsupported transport reached MCP network loader")

    monkeypatch.setattr(mcp_adapter, "load_mcp_tools_as_agent_tools", unexpected_loader)
    result = await probe_mcp_connection(
        MCPConnectionTest(
            name="local-index",
            transport="unrecognized",
            config={"url": "http://127.0.0.1:8765/mcp"},
        ),
        db=None,
    )

    assert result.success is False
    assert result.message == "Unsupported MCP transport."
