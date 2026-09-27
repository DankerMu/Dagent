"""Retired catalog selections do not leak through template API responses."""

from unittest.mock import patch

import pytest

from xagent.web.models.database import get_db
from xagent.web.models.mcp import MCPServer, UserMCPServer
from xagent.web.models.public_mcp import PublicMCPApp, PublicMCPAppAudit

# Reuse the template API app, isolated database, auth, and authoring fixtures.
from . import test_templates_api as fixtures

admin_headers = fixtures.admin_headers
admin_user = fixtures.admin_user
client = fixtures.client
mock_app_state = fixtures.mock_app_state
template_manager = fixtures.template_manager
templates_dir = fixtures.templates_dir
test_db = fixtures.test_db


def test_retired_catalog_template_connection_is_not_advertised(
    mock_app_state, admin_headers
):
    template = mock_app_state.template_manager._templates_cache["customer_support"]
    template["connections"] = [{"name": "Gmail"}, {"name": "Private Notes"}]
    template["agent_config"]["tool_categories"] += [
        "mcp:Gmail",
        "mcp:Private Notes",
    ]
    db = next(get_db())
    try:
        db.add(PublicMCPApp(app_id="gmail", name="Gmail", transport="stdio"))
        db.commit()
    finally:
        db.close()

    with patch.object(client.app, "state", mock_app_state):
        listing = client.get("/api/templates/", headers=admin_headers)
        detail = client.get("/api/templates/customer_support", headers=admin_headers)

    assert listing.status_code == detail.status_code == 200
    card = next(item for item in listing.json() if item["id"] == "customer_support")
    for exposed in (card, detail.json()):
        assert exposed["connections"] == [{"name": "Private Notes", "logo": None}]
        assert exposed["tool_categories"] == ["web_search", "mcp:Private Notes"]
    assert detail.json()["agent_config"]["tool_categories"] == [
        "web_search",
        "mcp:Private Notes",
    ]


@pytest.mark.parametrize(
    ("command", "owner", "visible"),
    [(None, False, False), ("catalog-run", True, True), ("lan-local", False, True)],
)
def test_deleted_catalog_template_selection_respects_existing_lan_servers(
    mock_app_state, admin_headers, admin_user, command, owner, visible
):
    template = mock_app_state.template_manager._templates_cache["customer_support"]
    template["connections"] = [
        {"name": "LAN Catalog"},
        {"name": "Earlier Catalog"},
        {"name": "Private Notes"},
    ]
    template["agent_config"]["tool_categories"] += [
        "mcp:LAN Catalog",
        "mcp:Earlier Catalog",
        "mcp:Private Notes",
    ]
    earlier = {
        "app_id": "lan_catalog",
        "name": "Earlier Catalog",
        "description": None,
        "icon": None,
        "transport": "stdio",
        "provider_name": None,
        "category": None,
        "oauth_scopes": None,
        "is_visible_in_connector": True,
        "launch_config": {"command": "catalog-run", "args": [], "required_env": []},
    }
    latest = {**earlier, "name": "LAN Catalog"}
    db = next(get_db())
    try:
        db.add_all(
            [
                PublicMCPAppAudit(
                    action="create",
                    app_id="lan_catalog",
                    after_values=earlier,
                ),
                PublicMCPAppAudit(
                    action="update",
                    app_id="lan_catalog",
                    before_values=earlier,
                    after_values=latest,
                ),
                PublicMCPAppAudit(
                    action="delete",
                    app_id="lan_catalog",
                    before_values=latest,
                ),
            ]
        )
        if command is not None:
            server = MCPServer(
                name="lan_catalog",
                managed="external",
                transport="stdio",
                command=command,
                auth=None,
            )
            db.add(server)
            db.flush()
            db.add(
                UserMCPServer(
                    user_id=admin_user["id"],
                    mcpserver_id=server.id,
                    is_owner=owner,
                    is_active=True,
                )
            )
        db.commit()
    finally:
        db.close()

    with patch.object(client.app, "state", mock_app_state):
        detail = client.get("/api/templates/customer_support", headers=admin_headers)

    assert detail.status_code == 200, detail.text
    names = {item["name"] for item in detail.json()["connections"]}
    categories = detail.json()["agent_config"]["tool_categories"]
    assert ("LAN Catalog" in names) is visible
    assert ("mcp:LAN Catalog" in categories) is visible
    assert "Earlier Catalog" not in names
    assert "mcp:Earlier Catalog" not in categories
    assert "Private Notes" in names
