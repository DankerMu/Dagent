"""MCP initialization errors stay sanitized under strict server policy."""

import pytest

from tests.core.tools.adapters.vibe.test_selection_spec import _MCPConfig
from xagent.core.tools.adapters.vibe import mcp_tools
from xagent.core.tools.adapters.vibe.config import (
    MCPFailurePolicy,
    MCPUnavailableSummary,
    RequiredMCPUnavailableError,
)
from xagent.core.tools.adapters.vibe.factory import ToolFactory


@pytest.mark.asyncio
async def test_unexpected_mcp_loader_failure_is_reported_without_secrets(
    monkeypatch, caplog
):
    async def fail_network_initialize(configs, sandbox=None):
        raise RuntimeError("Authorization: Bearer private-token")

    monkeypatch.setattr(
        ToolFactory,
        "_create_mcp_tools_from_configs",
        staticmethod(fail_network_initialize),
    )
    config = _MCPConfig(
        [
            {
                "name": "Local Notes",
                "transport": "streamable_http",
                "config": {"url": "http://127.0.0.1:8765/mcp"},
            }
        ],
        failure_policy=MCPFailurePolicy.STRICT,
    )

    with pytest.raises(RequiredMCPUnavailableError) as caught:
        await mcp_tools.create_mcp_tools(config)

    assert caught.value.summaries == (
        MCPUnavailableSummary("Local Notes", "loader_failed"),
    )
    assert config.load_summaries[0].failures == caught.value.summaries
    assert "private-token" not in str(caught.value) + caplog.text


@pytest.mark.asyncio
async def test_unrestricted_mcp_scan_failure_retains_all_server_errors():
    from xagent.core.tools.adapters.vibe.config import MCPConfigLoadError

    config = _MCPConfig(
        [],
        selection_spec=None,
        load_error=MCPConfigLoadError(["Local Notes", "Records"]),
        failure_policy=MCPFailurePolicy.STRICT,
    )

    with pytest.raises(RequiredMCPUnavailableError) as caught:
        await mcp_tools.create_mcp_tools(config)

    assert caught.value.summaries == (
        MCPUnavailableSummary("Local Notes", "config_load_failed"),
        MCPUnavailableSummary("Records", "config_load_failed"),
    )
