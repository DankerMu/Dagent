"""A waiting channel consumer must not mistake stale work for its own result."""

# Pytest fixture imports are intentionally shadowed by test parameters.
# ruff: noqa: F811

from unittest.mock import Mock

import pytest

from tests.web.services.channel_delivery_shared import (  # noqa: F401
    database_url as database_url,
)
from tests.web.services.channel_delivery_shared import (  # noqa: F401
    selected as selected,
)
from xagent.web.models.database import get_session_local
from xagent.web.models.task import Task, TaskStatus
from xagent.web.models.task_command import TaskExecutionCommand
from xagent.web.services import shared_channel_execution as shared
from xagent.web.services.task_lease_service import TaskLeaseLostError
from xagent.web.services.task_orchestrator import TaskTurnPayload


def test_worker_crash_settlement_returns_persisted_failure_without_result_payload(
    selected,
):
    command_id = shared._accept_channel_turn(
        selected, TaskTurnPayload("local request"), "ingress"
    )
    with get_session_local()() as db:
        task = db.get(Task, selected.selection.task_id)
        task.run_id = selected.run_id
        task.status = TaskStatus.FAILED
        task.output = "failed after restart"
        db.commit()

    assert shared._read_channel_result(command_id, selected.run_id) == {
        "success": False,
        "status": "failed",
        "output": "failed after restart",
    }
    with pytest.raises(TaskLeaseLostError, match="identity changed"):
        shared._read_channel_result(command_id, "unrelated-run")


@pytest.mark.asyncio
async def test_stopped_unaccepted_channel_turn_never_stages_start(
    selected, monkeypatch
):
    bridge = Mock(host_id="ingress")
    bridge.register_origin.return_value = "origin"
    monkeypatch.setattr(shared, "get_task_event_bridge", lambda: bridge)
    selected.request_stop()

    result = await selected.execute(TaskTurnPayload("cancelled request"), None)
    await selected.close()

    assert result == {"success": True, "status": "interrupted"}
    with get_session_local()() as db:
        task = db.get(Task, selected.selection.task_id)
        assert task.status == TaskStatus.PAUSED
        assert db.query(TaskExecutionCommand).count() == 0
