"""A local MCP server's owner controls its configuration over HTTP."""

import pytest

from .conftest import _admin_headers, _register_second_user, client

pytestmark = pytest.mark.usefixtures("_test_db")


def test_local_stdio_owner_can_create_edit_toggle_and_delete_without_granting_other_users():
    owner = _admin_headers()
    other = _register_second_user("other_user", "otherpass1")
    created = client.post(
        "/api/mcp/servers",
        headers=owner,
        json={
            "name": "local-notes",
            "transport": "stdio",
            "config": {"command": "python", "args": ["-m", "local_notes"]},
        },
    )
    assert created.status_code == 201, created.text
    server_id = created.json()["id"]
    duplicate = client.post(
        "/api/mcp/servers",
        headers=other,
        json={
            "name": "local-notes",
            "transport": "stdio",
            "config": {"command": "malicious-replacement"},
        },
    )
    assert duplicate.status_code == 400
    assert (
        client.get(f"/api/mcp/servers/{server_id}", headers=owner).json()["config"][
            "command"
        ]
        == "python"
    )

    assert client.get(f"/api/mcp/servers/{server_id}", headers=other).status_code == 404
    denied = client.put(
        f"/api/mcp/servers/{server_id}",
        headers=other,
        json={"description": "takeover"},
    )
    assert denied.status_code == 404

    updated = client.put(
        f"/api/mcp/servers/{server_id}",
        headers=owner,
        json={"description": "LAN-only index"},
    )
    assert updated.status_code == 200, updated.text
    assert (
        client.get(f"/api/mcp/servers/{server_id}", headers=owner).json()["description"]
        == "LAN-only index"
    )
    assert client.get(f"/api/mcp/servers/{server_id}", headers=other).status_code == 404

    assert (
        client.post(f"/api/mcp/servers/{server_id}/toggle", headers=other).status_code
        == 404
    )
    disabled = client.post(f"/api/mcp/servers/{server_id}/toggle", headers=owner)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["is_active"] is False
    assert (
        client.get(f"/api/mcp/servers/{server_id}", headers=owner).json()["is_active"]
        is False
    )
    enabled = client.post(f"/api/mcp/servers/{server_id}/toggle", headers=owner)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["is_active"] is True
    logs = client.get(f"/api/mcp/servers/{server_id}/logs", headers=owner)
    assert logs.status_code == 400
    assert logs.json()["detail"] == "Logs only available for internal servers"

    deleted = client.delete(f"/api/mcp/servers/{server_id}", headers=owner)
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/api/mcp/servers/{server_id}", headers=owner).status_code == 404


def test_new_mcp_connection_cannot_accept_a_mask_without_a_stored_secret():
    owner = _admin_headers()
    response = client.post(
        "/api/mcp/servers",
        headers=owner,
        json={
            "name": "local-masked",
            "transport": "stdio",
            "config": {"command": "python"},
            "user_env": {"API_TOKEN": "********"},
        },
    )

    assert response.status_code == 400
    global_mask = client.post(
        "/api/mcp/servers",
        headers=owner,
        json={
            "name": "global-masked",
            "transport": "stdio",
            "config": {"command": "python", "env": {"API_TOKEN": "********"}},
        },
    )
    assert global_mask.status_code == 400
    listing = client.get("/api/mcp/servers", headers=owner)
    assert listing.status_code == 200
    assert "local-masked" not in [item["name"] for item in listing.json()]
    assert "global-masked" not in [item["name"] for item in listing.json()]
