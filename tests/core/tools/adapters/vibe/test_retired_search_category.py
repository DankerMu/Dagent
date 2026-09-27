"""Retiring search cannot silently grant broader basic-tool permissions."""

import pytest

from tests.core.tools.adapters.vibe.test_selection_spec import _mock_tool
from xagent.core.tools.adapters.vibe.selection_spec import ToolSelectionSpec


@pytest.mark.parametrize("keep_basic", [False, True])
def test_retired_search_warns_without_broadening_grants(caplog, keep_basic):
    categories = ["basic", "web_search"] if keep_basic else ["web_search"]
    tools = [
        _mock_tool("fetch_web_content", "basic"),
        _mock_tool("download_web_asset", "basic"),
        _mock_tool("execute_python_code", "basic"),
    ]
    with caplog.at_level("WARNING"):
        spec = ToolSelectionSpec.from_raw(tool_categories=categories)
    allowed = spec.compute_allowed_names(tools)
    expected = (
        frozenset({"fetch_web_content", "download_web_asset", "execute_python_code"})
        if keep_basic
        else frozenset()
    )
    assert allowed == expected
    assert any("web_search" in record.getMessage() for record in caplog.records)
