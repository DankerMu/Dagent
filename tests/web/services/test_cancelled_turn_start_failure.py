"""A cancelled caller retains task serialization until its failed claim settles."""

import asyncio

import pytest

from xagent.web.services.task_orchestrator import (
    TaskTurnOrchestrator,
    TaskTurnPayload,
    TurnKind,
)


@pytest.mark.asyncio
async def test_cancelled_start_failure_does_not_release_gate_early(monkeypatch):
    entered = asyncio.Event()
    cancelled_wait = asyncio.Event()
    release = asyncio.Event()
    second_entered = asyncio.Event()

    async def failing_start(**kwargs):
        if entered.is_set():
            second_entered.set()
            return "second turn"
        entered.set()
        await release.wait()
        raise OSError("Claim failed after caller cancellation")

    monkeypatch.setattr(TaskTurnOrchestrator, "_begin_turn_unserialized", failing_start)
    arguments = dict(
        task_id=99421,
        task_owner_user_id=1,
        payload=TaskTurnPayload("hello"),
        kind=TurnKind.CREATE,
    )
    first = asyncio.create_task(TaskTurnOrchestrator.begin_turn(**arguments))
    second = None
    try:
        await asyncio.wait_for(entered.wait(), 2)
        first.cancel()
        # A scheduled barrier lets cancellation enter the public method's shield.
        asyncio.get_running_loop().call_soon(cancelled_wait.set)
        await cancelled_wait.wait()
        second = asyncio.create_task(TaskTurnOrchestrator.begin_turn(**arguments))
        barrier = asyncio.Event()
        asyncio.get_running_loop().call_soon(barrier.set)
        await barrier.wait()
        assert not second_entered.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(first, 2)
        assert await asyncio.wait_for(second, 2) == "second turn"
    finally:
        release.set()
        for operation in (first, second):
            if operation is not None and not operation.done():
                operation.cancel()
        await asyncio.gather(
            *[operation for operation in (first, second) if operation is not None],
            return_exceptions=True,
        )
