"""Heartbeat-shutdown cancellation must settle the lease before propagating.

When ``_schedule_bg`` is cancelled while awaiting heartbeat stop, that
``CancelledError`` is retained so lease settlement and connector cleanup still
run. Escaping immediately leaves a RUNNING owner with no coroutine; swallowing
it reports a finished turn that the caller never asked to finish.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from tests.shared.db_teardown import drop_all_tables
from tests.web.pool_contention_shared import GUARD_TIMEOUT
from tests.web.services.test_task_orchestrator import (
    _delivery_status,
    _finalize_runner_patches,
    _finalize_turn_fixture,
    _spawn_finalize_runner,
    _store_runtime_secret_for_turn,
)
from xagent.web.models import database
from xagent.web.models.database import get_db, get_engine, init_db
from xagent.web.models.task import Task, TaskStatus
from xagent.web.services import task_orchestrator as task_orchestrator_module
from xagent.web.services.chat_history_service import DELIVERY_COMPLETED
from xagent.web.services.connector_runtime import get_ephemeral_runtime_values
from xagent.web.services.task_execution import background_task_manager
from xagent.web.services.task_lease_service import TaskLeaseHeartbeatOutcome


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    init_db(db_url=f"sqlite:///{tmp_path / 'orchestrator-heartbeat-cancel.db'}")
    db = next(get_db())
    try:
        yield db
    finally:
        db.close()
        drop_all_tables(get_engine())
        get_engine().dispose()


@pytest.fixture(autouse=True)
def _clear_bg_manager(monkeypatch):
    monkeypatch.setattr(background_task_manager, "running_tasks", {})


@pytest.mark.asyncio
async def test_heartbeat_shutdown_cancellation_settles_lease_before_propagate(
    db_session,
) -> None:
    """Cancel during heartbeat stop, after execution has already returned.

    Rejects dropping the ``CancelledError`` handler around heartbeat shutdown:
    settlement would never run and the lease would stay owned. Rejects
    swallowing that cancellation after cleanup: the background handle would
    complete successfully. Execution already returned, so catching shutdown
    cancellation uncancels the runner, settlement commits, and delivery closes
    as ``completed`` before the retained cancellation is re-raised.
    """

    user, task, payload, lease = _finalize_turn_fixture(
        db_session, turn_id="turn-heartbeat-shutdown-cancel"
    )
    _store_runtime_secret_for_turn(payload.turn_id)
    assert get_ephemeral_runtime_values(payload.turn_id) is not None

    execution_finished = asyncio.Event()
    heartbeat_saw_stop = asyncio.Event()
    allow_heartbeat_release = asyncio.Event()

    async def execute(**_kwargs) -> None:
        execution_finished.set()

    async def heartbeat(_lease, stop_event: asyncio.Event) -> TaskLeaseHeartbeatOutcome:
        await stop_event.wait()
        heartbeat_saw_stop.set()
        await allow_heartbeat_release.wait()
        return TaskLeaseHeartbeatOutcome()

    bg_task: asyncio.Task[None] | None = None
    try:
        with (
            _finalize_runner_patches(lease, execute=execute),
            patch.object(
                task_orchestrator_module, "run_task_lease_heartbeat", heartbeat
            ),
        ):
            bg_task = _spawn_finalize_runner(task, user, payload)
            await asyncio.wait_for(execution_finished.wait(), timeout=GUARD_TIMEOUT)
            await asyncio.wait_for(heartbeat_saw_stop.wait(), timeout=GUARD_TIMEOUT)
            assert not bg_task.done()

            bg_task.cancel()
            assert not bg_task.done()

            allow_heartbeat_release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(bg_task, timeout=GUARD_TIMEOUT)
    finally:
        allow_heartbeat_release.set()
        if bg_task is not None and not bg_task.done():
            await asyncio.wait_for(
                asyncio.gather(bg_task, return_exceptions=True),
                timeout=GUARD_TIMEOUT,
            )

    db_session.expire_all()
    stored = db_session.get(Task, int(task.id))
    assert stored is not None
    assert stored.runner_id is None
    assert stored.status == TaskStatus.FAILED
    assert (
        _delivery_status(db_session, "turn-heartbeat-shutdown-cancel")
        == DELIVERY_COMPLETED
    )
    assert get_ephemeral_runtime_values(payload.turn_id) is None
