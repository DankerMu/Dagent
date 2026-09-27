"""Selection of real generic HTTP and custom-API tools."""

from xagent.core.tools.adapters.vibe.api_tool import APITool
from xagent.core.tools.adapters.vibe.api_tool_adapter import CustomApiTool
from xagent.core.tools.adapters.vibe.base import ToolCategory
from xagent.core.tools.adapters.vibe.function import FunctionTool
from xagent.core.tools.adapters.vibe.selection_spec import (
    ToolSelectionSpec,
    with_mcp_tools,
    without_published_agent_tools,
)


def test_named_lan_api_selection_does_not_grant_arbitrary_http_access():
    generic = APITool()
    configured = CustomApiTool(name="Local API", description="LAN", env={})
    unrelated = CustomApiTool(name="Other API", description="LAN", env={})

    selected = ToolSelectionSpec.from_raw(
        tool_categories=["mcp: LOCAL-API"]
    ).compute_allowed_names([generic, configured, unrelated])

    assert selected == frozenset({configured.name})


def test_basic_category_allows_generic_http_without_granting_named_api():
    generic = APITool()
    configured = CustomApiTool(name="Local API", description="LAN", env={})

    selected = ToolSelectionSpec.from_raw(
        tool_categories=["basic"]
    ).compute_allowed_names([generic, configured])

    assert selected == frozenset({"api_call"})


def test_actor_enables_only_explicit_lan_connector_from_zero_tool_selection():
    connector = FunctionTool(lambda: "connected", name="lan_connector")
    connector.category = ToolCategory.MCP
    generic = APITool()
    original = ToolSelectionSpec.from_raw(tool_categories=[])

    selected = with_mcp_tools(original).compute_allowed_names([connector, generic])

    assert selected == frozenset({"lan_connector"})
    assert original.compute_allowed_names([connector, generic]) == frozenset()


def test_actor_drops_published_delegation_without_granting_basic_tools():
    delegate = FunctionTool(lambda: "delegated", name="delegated_agent")
    delegate.category = ToolCategory.AGENT
    generic = APITool()
    original = ToolSelectionSpec.from_raw(
        extras_only_when_unconfigured=True,
        published_agent_ids=[42],
        name_allowlist={"delegated_agent"},
    )

    assert original.compute_allowed_names([delegate, generic]) == frozenset(
        {"delegated_agent"}
    )
    selected = without_published_agent_tools(original)
    assert selected.compute_allowed_names([delegate, generic]) == frozenset()


def test_task_contributed_tool_needs_explicit_name_authorization():
    contributed = FunctionTool(lambda: "local result", name="local_extension")
    generic = APITool()
    selection = ToolSelectionSpec.from_raw(tool_categories=["basic"])

    assert selection.compute_allowed_names([contributed, generic]) == frozenset(
        {"api_call"}
    )
    assert selection.compute_allowed_names(
        [contributed, generic], extension_tool_names=frozenset({"local_extension"})
    ) == frozenset({"api_call", "local_extension"})
