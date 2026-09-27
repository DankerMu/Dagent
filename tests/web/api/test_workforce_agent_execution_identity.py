"""A workforce run must not disclose one worker's events under another build ID."""

from datetime import datetime, timezone

import pytest

from xagent.web.models.task import Task, TaskStatus, TraceEvent
from xagent.web.models.user import User
from xagent.web.models.workforce import WorkforceRun

from .conftest import _admin_headers, _direct_db_session, client

pytestmark = pytest.mark.usefixtures("_test_db")


def test_agent_execution_url_requires_events_for_requested_worker() -> None:
    headers = _admin_headers()
    agent_ids = []
    for name in ("Trace manager", "Trace worker"):
        created = client.post(
            "/api/agents",
            headers=headers,
            json={
                "name": name,
                "instructions": "Handle a delegated task.",
                "execution_mode": "balanced",
            },
        )
        assert created.status_code == 200, created.text
        agent_id = created.json()["id"]
        published = client.post(f"/api/agents/{agent_id}/publish", headers=headers)
        assert published.status_code == 200, published.text
        agent_ids.append(agent_id)

    created_workforce = client.post(
        "/api/workforces",
        headers=headers,
        json={
            "name": "Scoped worker traces",
            "manager_agent_id": agent_ids[0],
            "manager_instructions": "Delegate work.",
            "workers": [
                {
                    "source_type": "existing",
                    "agent_id": agent_ids[1],
                    "alias": "worker",
                    "assignment_instructions": "Execute the task.",
                    "enabled": True,
                    "sort_order": 1,
                }
            ],
        },
    )
    assert created_workforce.status_code == 200, created_workforce.text
    workforce_id = created_workforce.json()["id"]

    db = _direct_db_session()
    try:
        user = db.query(User).filter(User.username == "admin").one()
        task = Task(
            user_id=int(user.id),
            title="Scoped trace run",
            description="Delegate task",
            status=TaskStatus.RUNNING,
        )
        db.add(task)
        db.flush()
        task_id = int(task.id)
        db.add(
            WorkforceRun(
                workforce_id=workforce_id,
                task_id=task_id,
                user_id=int(user.id),
                status=TaskStatus.RUNNING.value,
                snapshot={"workforce": {"id": workforce_id}},
            )
        )
        db.add(
            TraceEvent(
                task_id=task_id,
                build_id="worker-one",
                event_id="worker-one-start",
                event_type="react_task_start",
                timestamp=datetime.now(timezone.utc),
                data={
                    "source": "xagent-agent-tool-child",
                    "worker_task_id": "worker-one",
                },
            )
        )
        db.commit()
    finally:
        db.close()

    url = f"/api/workforces/{workforce_id}/runs/{task_id}/agent-executions"
    own_trace = client.get(f"{url}/worker-one", headers=headers)
    assert own_trace.status_code == 200, own_trace.text
    assert [event["event_id"] for event in own_trace.json()["trace_events"]] == [
        "worker-one-start"
    ]

    missing_worker = client.get(f"{url}/worker-two", headers=headers)
    assert missing_worker.status_code == 404
