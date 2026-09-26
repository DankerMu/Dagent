"""Public HTTP smoke against a real isolated host with a seeded password."""

from __future__ import annotations

import pytest

from tests.e2e.runtime_proof import (
    COLLECTION_NAME,
    PROOF_OWNER_PASSWORD,
    PROOF_OWNER_USERNAME,
)

pytestmark = pytest.mark.e2e


def test_health_and_ready_are_unauthenticated(proof_app):
    health = proof_app.client.get("/health")
    assert health.status_code == 200, health.text
    assert health.json()["status"] == "ok"
    ready = proof_app.client.get("/ready")
    assert ready.status_code == 200, ready.text
    assert ready.json()["status"] == "ready"


def test_login_me_and_unauthorized_rejection(proof_app):
    missing = proof_app.client.get("/api/auth/me")
    assert missing.status_code in {401, 403}, missing.text
    forged = proof_app.client.get(
        "/api/auth/me", headers={"Authorization": "Bearer not-a-token"}
    )
    assert forged.status_code == 401, forged.text
    login = proof_app.client.post(
        "/api/auth/login",
        json={"username": PROOF_OWNER_USERNAME, "password": PROOF_OWNER_PASSWORD},
    )
    assert login.status_code == 200, login.text
    body = login.json()
    assert body["success"] is True
    token = body["access_token"]
    assert token
    me = proof_app.client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert me.status_code == 200, me.text
    profile = me.json()
    assert profile["success"] is True
    assert profile["user"]["username"] == PROOF_OWNER_USERNAME
    wrong = proof_app.client.post(
        "/api/auth/login",
        json={
            "username": PROOF_OWNER_USERNAME,
            "password": "wrong-password",  # pragma: allowlist secret - rejection fixture
        },
    )
    assert wrong.status_code == 401, wrong.text


def test_tasks_list_is_empty_before_any_create(proof_app):
    response = proof_app.client.get("/api/chat/tasks", headers=proof_app.headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["tasks"] == []
    assert body["pagination"]["total_count"] == 0


def test_kb_collection_create_read_and_delete_without_ingest(proof_app):
    created = proof_app.client.post(
        f"/api/kb/collections/{COLLECTION_NAME}/config",
        headers=proof_app.headers,
        json={"chunk_size": 1000, "chunk_overlap": 200},
    )
    assert created.status_code == 200, created.text
    payload = created.json()
    assert payload["status"] == "success"
    assert payload["collection"] == COLLECTION_NAME
    listed = proof_app.client.get("/api/kb/collections", headers=proof_app.headers)
    assert listed.status_code == 200, listed.text
    names = [item["name"] for item in listed.json()["collections"]]
    assert COLLECTION_NAME in names
    deleted = proof_app.client.delete(
        f"/api/kb/collections/{COLLECTION_NAME}", headers=proof_app.headers
    )
    assert deleted.status_code == 200, deleted.text
    after = proof_app.client.get("/api/kb/collections", headers=proof_app.headers)
    assert after.status_code == 200, after.text
    remaining = [item["name"] for item in after.json()["collections"]]
    assert COLLECTION_NAME not in remaining
