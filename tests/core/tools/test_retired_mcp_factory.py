"""Archived catalog servers must never reach the MCP runtime loader."""

from unittest.mock import patch

from tests.core.tools import test_mcp_database_integration as fixtures
from xagent.core.tools.adapters.vibe.factory import ToolFactory
from xagent.core.tools.adapters.vibe.mcp_adapter import MCPLoadResult
from xagent.core.tools.core.mcp.manager.db import DatabaseMCPServerManager, MCPServer
from xagent.web.models.mcp import UserMCPServer
from xagent.web.models.public_mcp import PublicMCPApp, PublicMCPAppAudit
from xagent.web.services.retired_mcp_catalog import is_retired_catalog_server
from xagent.web.tools.config import WebToolConfig

sample_stdio_config = fixtures.sample_stdio_config
test_db = fixtures.test_db


def _app_snapshot(app_id: str, name: str, command: str, args: list[str]) -> dict:
    # Matches the historical admin audit's full PublicMCPAppBase projection.
    return {
        "app_id": app_id,
        "name": name,
        "description": None,
        "icon": None,
        "transport": "stdio",
        "provider_name": None,
        "category": None,
        "oauth_scopes": None,
        "is_visible_in_connector": True,
        "launch_config": {
            "command": command,
            "args": args,
            "required_env": [],
        },
    }


@patch("xagent.core.tools.adapters.vibe.mcp_adapter.load_mcp_tools_as_agent_tools")
async def test_retired_catalog_stdio_server_cannot_execute_from_database_path(
    mock_load_mcp, test_db, sample_stdio_config
):
    manager = DatabaseMCPServerManager(test_db)
    manager.add_server(manager.create_config(**sample_stdio_config))
    server = test_db.query(MCPServer).filter_by(name=sample_stdio_config["name"]).one()
    server.auth = {"builtin_provenance": "catalog"}
    test_db.commit()

    tools = await ToolFactory.create_mcp_tools(test_db)

    mock_load_mcp.assert_not_called()
    assert len(tools) == 1
    result = tools[0].run_json_sync({})
    assert result["reason"] == "catalog_app_retired"
    assert result["unavailable_server"] == sample_stdio_config["name"]


async def test_deleted_catalog_earlier_launch_cannot_reach_runtime(test_db):
    before = _app_snapshot("retired_lan", "Retired LAN", "previous-launch", ["serve"])
    after = _app_snapshot("retired_lan", "Retired LAN", "replacement-launch", ["serve"])
    test_db.add_all(
        [
            PublicMCPAppAudit(
                action="create",
                app_id="retired_lan",
                after_values=before,
            ),
            PublicMCPAppAudit(
                action="update",
                app_id="retired_lan",
                before_values=before,
                after_values=after,
            ),
            PublicMCPAppAudit(
                action="delete",
                app_id="retired_lan",
                before_values=after,
            ),
        ]
    )
    server = MCPServer(
        name="retired_lan",
        managed="external",
        transport="stdio",
        command="previous-launch",
        args=["serve"],
        auth=None,
    )
    test_db.add(server)
    test_db.flush()
    test_db.add(
        UserMCPServer(user_id=1, mcpserver_id=server.id, is_owner=False, is_active=True)
    )
    test_db.commit()
    assert test_db.query(PublicMCPApp).filter_by(app_id="retired_lan").count() == 0
    assert is_retired_catalog_server(test_db, server)

    config = await WebToolConfig(
        db=test_db, request=None, user_id=1
    )._build_mcp_server_config(
        server=server,
        user_env_by_id={},
        shared_env_by_id={},
        env_source_by_id={},
    )
    assert config["transport"] == "unavailable"
    assert config["config"]["reason"] == "catalog_app_retired"

    with patch(
        "xagent.core.tools.adapters.vibe.mcp_adapter.load_mcp_tools_as_agent_tools"
    ) as loader:
        tools = await ToolFactory.create_mcp_tools(test_db, user_id=1)
    loader.assert_not_called()
    assert tools[0].run_json_sync({})["reason"] == "catalog_app_retired"


async def test_deleted_catalog_does_not_block_unrelated_or_owned_lan_server(test_db):
    snapshot = _app_snapshot("lan_mcp", "LAN MCP", "catalog-run", [])
    test_db.add(
        PublicMCPAppAudit(action="delete", app_id="lan_mcp", before_values=snapshot)
    )
    server = MCPServer(
        name="lan_mcp",
        managed="external",
        transport="stdio",
        command="catalog-run",
        args=["--local-lan"],
        auth=None,
    )
    test_db.add(server)
    test_db.flush()
    association = UserMCPServer(
        user_id=1, mcpserver_id=server.id, is_owner=False, is_active=True
    )
    test_db.add(association)
    test_db.commit()
    assert not is_retired_catalog_server(test_db, server)

    config = await WebToolConfig(
        db=test_db, request=None, user_id=1
    )._build_mcp_server_config(
        server=server,
        user_env_by_id={},
        shared_env_by_id={},
        env_source_by_id={},
    )
    assert config["transport"] == "stdio"
    assert config["config"]["command"] == "catalog-run"
    assert config["config"]["args"] == ["--local-lan"]
    with patch(
        "xagent.core.tools.adapters.vibe.mcp_adapter.load_mcp_tools_as_agent_tools"
    ) as loader:
        loader.return_value = MCPLoadResult(
            tools=(), loaded_servers=("lan_mcp",), failures=()
        )
        await ToolFactory.create_mcp_tools(test_db, user_id=1)
    assert loader.call_args.args[0]["lan_mcp"]["args"] == ["--local-lan"]

    server.args = []
    association.is_owner = True
    test_db.commit()
    assert not is_retired_catalog_server(test_db, server)
    with patch(
        "xagent.core.tools.adapters.vibe.mcp_adapter.load_mcp_tools_as_agent_tools"
    ) as loader:
        loader.return_value = MCPLoadResult(
            tools=(), loaded_servers=("lan_mcp",), failures=()
        )
        await ToolFactory.create_mcp_tools(test_db, user_id=1)
    assert loader.call_args.args[0]["lan_mcp"]["command"] == "catalog-run"


def test_connected_catalog_launch_survives_live_app_update(test_db):
    previous = _app_snapshot(
        "edited_catalog", "Edited Catalog", "previous-launch", ["serve"]
    )
    latest = _app_snapshot(
        "edited_catalog", "Edited Catalog", "replacement-launch", ["serve"]
    )
    test_db.add_all(
        [
            PublicMCPApp(
                app_id="edited_catalog",
                name="Edited Catalog",
                transport="stdio",
                launch_config=latest["launch_config"],
            ),
            PublicMCPAppAudit(
                action="create",
                app_id="edited_catalog",
                after_values=previous,
            ),
            PublicMCPAppAudit(
                action="update",
                app_id="edited_catalog",
                before_values=previous,
                after_values=latest,
            ),
        ]
    )
    server = MCPServer(
        name="edited_catalog",
        managed="external",
        transport="stdio",
        command="previous-launch",
        args=["serve"],
        auth=None,
    )
    test_db.add(server)
    test_db.flush()
    test_db.add(UserMCPServer(user_id=1, mcpserver_id=server.id, is_owner=False))
    test_db.commit()

    assert is_retired_catalog_server(test_db, server)
