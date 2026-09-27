from __future__ import annotations

import pytest

from xagent.web.models.mcp import MCPServer, UserMCPServer
from xagent.web.models.public_mcp import PublicMCPApp, PublicMCPAppAudit
from xagent.web.models.user import User

from .conftest import _admin_headers, _direct_db_session, _register_second_user, client

pytestmark = pytest.mark.usefixtures("_test_db")


def _uid(username: str) -> int:
    db = _direct_db_session()
    try:
        return int(db.query(User).filter(User.username == username).one().id)
    finally:
        db.close()


def _make_server_for(user_id: int, name: str) -> None:
    db = _direct_db_session()
    try:
        server = MCPServer(
            name=name, managed="external", transport="stdio", description="d"
        )
        db.add(server)
        db.flush()
        db.add(UserMCPServer(user_id=user_id, mcpserver_id=server.id, is_active=True))
        db.commit()
    finally:
        db.close()


def _deleted_stdio_catalog(db, *, app_id: str, name: str, command: str) -> None:
    # Admin writes full public-app snapshots; deletion leaves only its audit.
    snapshot = {
        "app_id": app_id,
        "name": name,
        "description": None,
        "icon": None,
        "transport": "stdio",
        "provider_name": None,
        "category": None,
        "oauth_scopes": None,
        "is_visible_in_connector": True,
        "launch_config": {"command": command, "args": ["serve"], "required_env": []},
    }
    db.add_all(
        [
            PublicMCPAppAudit(action="create", app_id=app_id, after_values=snapshot),
            PublicMCPAppAudit(action="delete", app_id=app_id, before_values=snapshot),
        ]
    )


def _connected_server(db, *, user_id: int, name: str, command: str, owner: bool) -> int:
    server = MCPServer(
        name=name,
        managed="external",
        transport="stdio",
        command=command,
        args=["serve"],
        auth=None,
    )
    db.add(server)
    db.flush()
    server_id = int(server.id)
    db.add(
        UserMCPServer(
            user_id=user_id, mcpserver_id=server_id, is_owner=owner, is_active=True
        )
    )
    return server_id


def test_deleted_custom_catalog_connection_is_not_listed():
    headers = _admin_headers()
    user_id = _uid("admin")
    db = _direct_db_session()
    try:
        _deleted_stdio_catalog(
            db, app_id="lan_catalog", name="LAN Catalog", command="catalog-run"
        )
        server_id = _connected_server(
            db, user_id=user_id, name="lan_catalog", command="catalog-run", owner=False
        )
        db.commit()
        assert db.query(PublicMCPApp).filter_by(app_id="lan_catalog").count() == 0
        assert db.query(PublicMCPAppAudit).filter_by(action="delete").count() == 1
    finally:
        db.close()

    listing = client.get("/api/mcp/servers", headers=headers)
    detail = client.get(f"/api/mcp/servers/{server_id}", headers=headers)
    assert listing.status_code == 200, listing.text
    assert "lan_catalog" not in [item["name"] for item in listing.json()]
    assert detail.status_code == 404


def test_owner_custom_server_with_deleted_catalog_name_stays_visible():
    headers = _admin_headers()
    user_id = _uid("admin")
    db = _direct_db_session()
    try:
        _deleted_stdio_catalog(
            db, app_id="lan_catalog", name="LAN Catalog", command="catalog-run"
        )
        _connected_server(
            db, user_id=user_id, name="lan_catalog", command="catalog-run", owner=True
        )
        db.commit()
    finally:
        db.close()

    listing = client.get("/api/mcp/servers", headers=headers)
    assert listing.status_code == 200, listing.text
    assert "lan_catalog" in [item["name"] for item in listing.json()]


def test_ownerless_migrated_lan_server_with_catalog_name_stays_visible():
    headers = _admin_headers()
    user_id = _uid("admin")
    db = _direct_db_session()
    try:
        _deleted_stdio_catalog(
            db, app_id="lan_catalog", name="LAN Catalog", command="catalog-run"
        )
        _connected_server(
            db,
            user_id=user_id,
            name="lan_catalog",
            command="lan-local",
            owner=False,
        )
        db.commit()
    finally:
        db.close()

    listing = client.get("/api/mcp/servers", headers=headers)
    assert listing.status_code == 200, listing.text
    assert "lan_catalog" in [item["name"] for item in listing.json()]


def test_admin_can_list_other_users_mcp_servers():
    admin = _admin_headers()
    _register_second_user("bob", "bobpass1")
    bob_id = _uid("bob")
    _make_server_for(bob_id, "bob-server")

    response = client.get(f"/api/mcp/servers?user_id={bob_id}", headers=admin)
    assert response.status_code == 200, response.text
    assert "bob-server" in [server["name"] for server in response.json()]


def test_non_admin_cannot_pass_user_id():
    _admin_headers()
    bob = _register_second_user("bob", "bobpass1")
    admin_id = _uid("admin")
    response = client.get(f"/api/mcp/servers?user_id={admin_id}", headers=bob)
    assert response.status_code == 403
