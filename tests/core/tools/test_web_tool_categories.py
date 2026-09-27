"""Tests for generic web fetch/download tool category metadata."""

from pathlib import Path

from xagent.core.tools.adapters.vibe.base import ToolCategory
from xagent.core.tools.adapters.vibe.download_web_asset import DownloadWebAssetTool
from xagent.core.tools.adapters.vibe.fetch_web_content import FetchWebContentTool
from xagent.core.workspace import TaskWorkspace


def test_web_fetch_tools_share_basic_category(tmp_path: Path) -> None:
    workspace = TaskWorkspace("web-asset-category", base_dir=str(tmp_path))
    tools = [
        FetchWebContentTool(),
        DownloadWebAssetTool(workspace),
    ]

    assert {tool.metadata.category for tool in tools} == {ToolCategory.BASIC}
