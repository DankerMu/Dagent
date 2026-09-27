"""Private share-link mutation must not expose another user's agent."""

import pytest

from .conftest import _admin_headers, _register_second_user, client

pytestmark = pytest.mark.usefixtures("_test_db")


def test_other_user_cannot_enable_or_rotate_agent_share_link() -> None:
    owner_headers = _admin_headers()
    created = client.post(
        "/api/agents",
        headers=owner_headers,
        json={
            "name": "Private share target",
            "instructions": "Assist the owner.",
            "execution_mode": "balanced",
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    published = client.post(f"/api/agents/{agent_id}/publish", headers=owner_headers)
    assert published.status_code == 200, published.text

    visitor_headers = _register_second_user()
    for url in (
        f"/api/agents/{agent_id}/share-link",
        f"/api/agents/{agent_id}/share-link/rotate",
    ):
        response = client.post(url, headers=visitor_headers)
        assert response.status_code == 404

    owner_state = client.get(
        f"/api/agents/{agent_id}/share-link", headers=owner_headers
    )
    assert owner_state.status_code == 200, owner_state.text
    assert owner_state.json()["share_enabled"] is False
