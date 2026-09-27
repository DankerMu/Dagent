"""A2A clients must not accept an output mode the agent cannot produce."""

import pytest

from xagent.web.models.task import Task

from .conftest import _admin_headers, _direct_db_session, client

pytestmark = pytest.mark.usefixtures("_test_db")


def test_unsupported_output_mode_rejects_message_before_creating_task() -> None:
    headers = _admin_headers()
    created = client.post(
        "/api/agents",
        headers=headers,
        json={
            "name": "Text-only agent",
            "instructions": "Respond with text.",
            "execution_mode": "balanced",
        },
    )
    assert created.status_code == 200, created.text
    agent_id = created.json()["id"]
    assert (
        client.post(f"/api/agents/{agent_id}/publish", headers=headers).status_code
        == 200
    )
    api_key = client.post(f"/api/agents/{agent_id}/api-key", headers=headers)
    assert api_key.status_code == 200, api_key.text

    response = client.post(
        f"/api/a2a/agents/{agent_id}/message:send",
        headers={
            "Authorization": f"Bearer {api_key.json()['full_key']}",
            "A2A-Version": "1.0",
        },
        json={
            "message": {
                "messageId": "unsupported-output",
                "role": "ROLE_USER",
                "parts": [{"text": "Produce an image"}],
            },
            "configuration": {"acceptedOutputModes": ["image/png"]},
        },
    )
    assert response.status_code == 400
    assert (
        response.json()["error"]["details"][0]["reason"] == "CONTENT_TYPE_NOT_SUPPORTED"
    )

    db = _direct_db_session()
    try:
        assert db.query(Task).filter(Task.agent_id == agent_id).count() == 0
    finally:
        db.close()
