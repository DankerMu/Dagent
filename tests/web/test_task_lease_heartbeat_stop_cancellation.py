"""``stop_task_lease_heartbeat`` distinguishes child vs caller cancellation.

The public stopper takes an already-running heartbeat task. A cancelled child
must drain and then resolve to an empty outcome so lease shutdown can continue.
A cancelled caller must still wait for that drain, then propagate cancellation
instead of reporting a successful stop.
"""

from __future__ import annotations

import asyncio

import pytest

from tests.web.pool_contention_shared import GUARD_TIMEOUT
from xagent.web.services.task_lease_service import (
    TaskLeaseHeartbeatOutcome,
    stop_task_lease_heartbeat,
)


async def _gated_heartbeat(
    started: asyncio.Event,
    cleanup_started: asyncio.Event,
    allow_cleanup: asyncio.Event,
    cleanup_finished: asyncio.Event,
) -> TaskLeaseHeartbeatOutcome:
    started.set()
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        cleanup_started.set()
        await allow_cleanup.wait()
        cleanup_finished.set()
        raise
    return TaskLeaseHeartbeatOutcome()


@pytest.mark.asyncio
async def test_stop_heartbeat_returns_after_cancelled_child_drains() -> None:
    """A cancelled heartbeat is drained, not re-raised as stopper failure.

    Rejects re-raising every ``CancelledError`` from ``await_task_settlement``:
    that would treat a child-only cancel as caller failure and abort lease
    shutdown. Also rejects returning before the child's gated cleanup finishes.
    """

    cleanup_started = asyncio.Event()
    started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _gated_heartbeat(started, cleanup_started, allow_cleanup, cleanup_finished)
    )
    stop_event = asyncio.Event()
    stopping: asyncio.Task[TaskLeaseHeartbeatOutcome] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=GUARD_TIMEOUT)
        heartbeat_task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=GUARD_TIMEOUT)

        stopping = asyncio.create_task(
            stop_task_lease_heartbeat(heartbeat_task, stop_event)
        )
        await asyncio.wait_for(stop_event.wait(), timeout=GUARD_TIMEOUT)
        assert not stopping.done()
        assert not cleanup_finished.is_set()

        allow_cleanup.set()
        outcome = await asyncio.wait_for(stopping, timeout=GUARD_TIMEOUT)
    finally:
        allow_cleanup.set()
        if stopping is not None and not stopping.done():
            await asyncio.wait_for(
                asyncio.gather(stopping, return_exceptions=True),
                timeout=GUARD_TIMEOUT,
            )
        elif not heartbeat_task.done():
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    assert cleanup_finished.is_set()
    assert heartbeat_task.done()
    assert isinstance(outcome, TaskLeaseHeartbeatOutcome)
    assert outcome.lease_lost is False
    assert outcome.pool_timeout is None
    assert outcome.requires_ttl_recovery is False


@pytest.mark.asyncio
async def test_stop_heartbeat_propagates_caller_cancel_after_child_drains() -> None:
    """Caller cancellation survives, but only after the child has settled.

    Rejects swallowing ``CancelledError`` whenever the child was already
    cancelled (the stopper would then look successful). Rejects propagating
    before gated child cleanup finishes. The stop event is observed first so
    cancellation lands inside settlement, not before the stopper starts.
    """

    cleanup_started = asyncio.Event()
    started = asyncio.Event()
    allow_cleanup = asyncio.Event()
    cleanup_finished = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _gated_heartbeat(started, cleanup_started, allow_cleanup, cleanup_finished)
    )
    stop_event = asyncio.Event()
    stopping: asyncio.Task[TaskLeaseHeartbeatOutcome] | None = None
    try:
        await asyncio.wait_for(started.wait(), timeout=GUARD_TIMEOUT)
        heartbeat_task.cancel()
        await asyncio.wait_for(cleanup_started.wait(), timeout=GUARD_TIMEOUT)

        stopping = asyncio.create_task(
            stop_task_lease_heartbeat(heartbeat_task, stop_event)
        )
        await asyncio.wait_for(stop_event.wait(), timeout=GUARD_TIMEOUT)
        stopping.cancel()
        cancellation_processed = asyncio.Event()
        asyncio.get_running_loop().call_soon(cancellation_processed.set)
        await cancellation_processed.wait()
        assert not stopping.done()
        assert not cleanup_finished.is_set()

        allow_cleanup.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(stopping, timeout=GUARD_TIMEOUT)
    finally:
        allow_cleanup.set()
        if stopping is not None and not stopping.done():
            await asyncio.wait_for(
                asyncio.gather(stopping, return_exceptions=True),
                timeout=GUARD_TIMEOUT,
            )
        elif not heartbeat_task.done():
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)

    assert cleanup_finished.is_set()
    assert heartbeat_task.done()
