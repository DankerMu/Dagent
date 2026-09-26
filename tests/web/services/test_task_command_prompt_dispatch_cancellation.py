"""Detached prompt-dispatch cancellation must drain without callback errors.

``dispatch_task_command_promptly`` detaches a still-running command after the
production 50ms handoff window. Dispatcher stop then cancels that detached
task. The done-callback must treat a cancelled result as observed completion
rather than an unhandled loop exception, and stop must wait for the executor's
gated cleanup so a processing claim is not abandoned mid-flight.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.web.pool_contention_shared import GUARD_TIMEOUT
from xagent.web.models import database
from xagent.web.models.database import Base, get_db, get_engine, init_db
from xagent.web.models.task import Task, TaskStatus
from xagent.web.models.task_command import TaskExecutionCommand
from xagent.web.models.user import User
from xagent.web.services.task_command_transport import (
    COMMAND_PROCESSING,
    TaskCommandKind,
    dispatch_task_command_promptly,
    enqueue_task_command,
    stop_task_command_dispatcher,
)


@pytest.fixture()
def db_session(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    init_db(db_url=f"sqlite:///{tmp_path / 'prompt-dispatch-cancel.db'}")
    db = next(get_db())
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=get_engine())
        get_engine().dispose()


def _create_claimable_task(db) -> tuple[User, Task]:
    user = User(username="prompt-cancel-user", password_hash="hash", is_admin=False)
    db.add(user)
    db.commit()
    task = Task(
        user_id=user.id,
        title="Prompt dispatch cancellation",
        description="Prompt dispatch cancellation",
        status=TaskStatus.RUNNING,
        execution_mode="auto",
        run_id="run-1",
        runner_id=None,
        lease_expires_at=None,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return user, task


@pytest.mark.asyncio
async def test_dispatcher_stop_drains_cancelled_detached_prompt_dispatch(
    db_session,
    monkeypatch,
) -> None:
    """Rejects three mutants of detached prompt-dispatch shutdown.

    - Deleting the cancelled-task guard lets ``task.result()`` raise
      ``CancelledError`` from the done callback as an unhandled loop error.
    - Returning from dispatcher stop before executor cleanup finishes abandons
      the in-flight claim while the handler is still draining.
    - Completing the command on cancellation would hide that a cancelled
      handler leaves the processing claim intact for expiry reclaim.
    """

    monkeypatch.setattr(
        "xagent.web.services.task_command_transport.get_shared_task_execution_enabled",
        lambda: False,
    )

    user, task = _create_claimable_task(db_session)
    enqueued = enqueue_task_command(
        db_session,
        task_id=int(task.id),
        actor_user_id=int(user.id),
        command_id="prompt-cancel",
        kind=TaskCommandKind.PAUSE,
        payload={"type": "pause_task"},
    )

    started = asyncio.Event()
    cleanup_started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    unhandled: list[BaseException] = []
    promptly: asyncio.Task[None] | None = None
    stopping: asyncio.Task[None] | None = None
    loop = asyncio.get_running_loop()
    previous_handler = loop.get_exception_handler()

    def capture_exception(
        handler_loop: asyncio.AbstractEventLoop, context: dict[str, object]
    ) -> None:
        exc = context.get("exception")
        if isinstance(exc, BaseException):
            unhandled.append(exc)
        if previous_handler is not None:
            previous_handler(handler_loop, context)
        else:
            handler_loop.default_exception_handler(context)

    async def execute(_command) -> dict[str, bool]:
        started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cleanup_started.set()
            await allow_cleanup.wait()
            cleanup_finished.set()
            raise
        return {"ok": True}

    loop.set_exception_handler(capture_exception)
    try:
        promptly = asyncio.create_task(
            dispatch_task_command_promptly(execute, command_db_id=enqueued.command_id)
        )
        await asyncio.wait_for(started.wait(), timeout=GUARD_TIMEOUT)
        await asyncio.wait_for(promptly, timeout=GUARD_TIMEOUT)

        stopping = asyncio.create_task(stop_task_command_dispatcher())
        await asyncio.wait_for(cleanup_started.wait(), timeout=GUARD_TIMEOUT)
        assert not stopping.done()
        assert not cleanup_finished.is_set()

        allow_cleanup.set()
        await asyncio.wait_for(stopping, timeout=GUARD_TIMEOUT)
        await asyncio.wait_for(cleanup_finished.wait(), timeout=GUARD_TIMEOUT)
    finally:
        allow_cleanup.set()
        loop.set_exception_handler(previous_handler)
        pending = [task for task in (promptly, stopping) if task is not None]
        if pending:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=GUARD_TIMEOUT,
            )
    assert unhandled == []
    db_session.expire_all()
    stored = db_session.get(TaskExecutionCommand, enqueued.command_id)
    assert stored is not None
    assert stored.status == COMMAND_PROCESSING
