"""Basic tools registration using @register_tool decorator."""

import logging
from typing import TYPE_CHECKING, Any, List

from .factory import ToolFactory, register_tool

if TYPE_CHECKING:
    from .config import BaseToolConfig

logger = logging.getLogger(__name__)


@register_tool(categories={"basic"})
async def create_basic_tools(config: "BaseToolConfig") -> List[Any]:
    """Create basic tools (fetch, download, code executors, HTTP)."""
    if not config.get_basic_tools_enabled():
        return []

    tools: List[Any] = []
    workspace = ToolFactory.create_workspace(config.get_workspace_config())

    from .fetch_web_content import FetchWebContentTool

    tools.append(FetchWebContentTool())

    if workspace:
        from .download_web_asset import DownloadWebAssetTool

        tools.append(DownloadWebAssetTool(workspace=workspace))

    # Python executor tool (if workspace available)
    if workspace:
        from .python_executor import PythonExecutorToolForBasic

        tools.append(PythonExecutorToolForBasic(workspace=workspace))

    # JavaScript executor tool (if workspace available)
    if workspace:
        from .javascript_executor import JavaScriptExecutorToolForBasic

        tools.append(JavaScriptExecutorToolForBasic(workspace=workspace))

    # API tool
    from .api_tool import APITool

    tools.append(APITool())

    # Command executor tool (if workspace available)
    if workspace:
        from .command_executor import CommandExecutorToolForBasic

        tools.append(CommandExecutorToolForBasic(workspace=workspace))

    return tools
