"""Standalone tool configuration must control observable output truncation."""

from xagent.core.tools.adapters.vibe.config import ToolConfig
from xagent.core.tools.adapters.vibe.factory import ToolFactory
from xagent.core.tools.adapters.vibe.function import FunctionTool


async def _nested_payload() -> dict:
    return {"records": [{"name": "Ada"}, {"name": "Grace"}], "status": "ready"}


async def test_environment_field_and_depth_limits_filter_tool_results(monkeypatch):
    monkeypatch.setenv("XAGENT_TOOL_MAX_FIELD_COUNT", "1")
    monkeypatch.setenv("XAGENT_TOOL_MAX_RECURSION_DEPTH", "1")
    tool = FunctionTool(_nested_payload, name="records")
    wrapped = ToolFactory._apply_output_filters([tool], ToolConfig({}))[0]

    result = await wrapped.run_json_async({})

    assert "status" not in result
    assert "records" in result
    assert not any(isinstance(item, dict) for item in result["records"])


async def test_invalid_environment_budgets_restore_untruncated_tool_result(monkeypatch):
    monkeypatch.setenv("XAGENT_TOOL_MAX_FIELD_COUNT", "invalid")
    monkeypatch.setenv("XAGENT_TOOL_MAX_RECURSION_DEPTH", "invalid")
    tool = FunctionTool(_nested_payload, name="records")
    wrapped = ToolFactory._apply_output_filters([tool], ToolConfig({}))[0]

    result = await wrapped.run_json_async({})

    assert result == {
        "records": [{"name": "Ada"}, {"name": "Grace"}],
        "status": "ready",
    }


async def test_explicit_budgets_override_environment_when_executing_tool(monkeypatch):
    monkeypatch.setenv("XAGENT_TOOL_MAX_FIELD_COUNT", "1")
    monkeypatch.setenv("XAGENT_TOOL_MAX_RECURSION_DEPTH", "1")
    config = ToolConfig({"max_field_count": "3", "max_recursion_depth": "3"})
    tool = FunctionTool(_nested_payload, name="records")
    wrapped = ToolFactory._apply_output_filters([tool], config)[0]

    result = await wrapped.run_json_async({})

    assert result == {
        "records": [{"name": "Ada"}, {"name": "Grace"}],
        "status": "ready",
    }
