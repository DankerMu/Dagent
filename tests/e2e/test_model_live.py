"""Real LAN model proof: register an explicit compatible endpoint and execute a task."""

from __future__ import annotations

import json

import pytest
from websockets.sync.client import connect

from tests.e2e.runtime_proof import (
    REAL_MODEL_ID,
    real_model_config,
)
from tests.e2e.shared_execution_harness import receive_event

pytestmark = [pytest.mark.e2e, pytest.mark.real_model, pytest.mark.requires_network]

PROMPT = "Reply with exactly these two tokens and nothing else: RUNTIME_PROOF 17"
EXPECTED_TOKEN = "RUNTIME_PROOF"
EXPECTED_NUMBER = "17"


def _register_and_pin_model(app, api_key: str, base_url: str, model_name: str) -> str:
    registered = app.client.post(
        "/api/models/register",
        headers=app.headers,
        json={
            "model_id": REAL_MODEL_ID,
            "category": "llm",
            "model_provider": "openai-compatible",
            "model_name": model_name,
            "api_key": api_key,
            "base_url": base_url,
        },
    )
    assert registered.status_code == 200, registered.text
    body = registered.json()
    db_id = body["id"]
    assert body["model_id"] == REAL_MODEL_ID
    assert body["model_name"] == model_name
    for config_type in ("general", "small_fast", "compact"):
        defaulted = app.client.post(
            "/api/models/user-default",
            headers=app.headers,
            json={"model_id": db_id, "config_type": config_type},
        )
        assert defaulted.status_code == 200, defaulted.text
    probe = app.client.post(
        "/api/models/test",
        headers=app.headers,
        json={"model_ids": [REAL_MODEL_ID]},
    )
    assert probe.status_code == 200, probe.text
    results = probe.json()
    assert results, probe.text
    assert any(item.get("model_id") == REAL_MODEL_ID for item in results), probe.text
    return REAL_MODEL_ID


def test_real_model_completes_pinned_flash_task(real_model_app):
    app = real_model_app
    base_url, model_name, api_key = real_model_config()
    model_id = _register_and_pin_model(app, api_key, base_url, model_name)
    created = app.client.post(
        "/api/chat/task/create",
        headers=app.headers,
        json={
            "title": "Real model proof",
            "description": PROMPT,
            "execution_mode": "flash",
            "llm_ids": [model_id, model_id, None, model_id],
        },
    )
    assert created.status_code == 200, created.text
    task = created.json()
    task_id = task["task_id"]
    assert task["model_id"] == model_id
    assert task["small_fast_model_id"] == model_id
    assert task["visual_model_id"] is None
    assert task["compact_model_id"] == model_id
    url = (
        str(app.client.base_url).replace("http://", "ws://").rstrip("/")
        + f"/ws/chat/{task_id}?token={app.token}"
    )
    with connect(url, open_timeout=30, close_timeout=30) as ws:
        receive_event(ws, "historical_data_complete", timeout=30)
        ws.send(json.dumps({"type": "execute_task"}))
        receive_event(ws, "task_completed", timeout=180)
    state = app.wait_task(task_id, timeout=30)
    assert state["status"] == "completed"
    output = str(state["output"] or "")
    fetched = app.client.get(f"/api/chat/task/{task_id}", headers=app.headers)
    assert fetched.status_code == 200, fetched.text
    detail = fetched.json()
    assert detail["status"] == "completed"
    assert detail["model_id"] == model_id
    normalized = " ".join(output.split())
    assert EXPECTED_TOKEN in normalized
    assert EXPECTED_NUMBER in normalized
    assert normalized != PROMPT
    if api_key:
        assert api_key not in output
