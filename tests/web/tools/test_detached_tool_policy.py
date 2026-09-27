"""A policy refresh without an ORM user cannot bypass application authorization."""

import pytest

from xagent.web.services.tool_credentials import (
    set_user_tool_allowlist_hook,
    set_user_tool_overrides_hook,
)
from xagent.web.tools.config import WebToolConfig


@pytest.mark.asyncio
async def test_policy_refresh_without_runtime_user_denies_all_tools():
    set_user_tool_allowlist_hook(lambda db, user: ["shell"])
    set_user_tool_overrides_hook(lambda db, user: {"file": {"enabled": False}})
    try:
        cfg = WebToolConfig(db=object(), request=None, user_id=7)
        await cfg.refresh_runtime_policy()
        assert cfg.get_user_tool_allowlist() == []
        assert cfg.get_user_tool_overrides() == {}
    finally:
        set_user_tool_allowlist_hook(None)
        set_user_tool_overrides_hook(None)
