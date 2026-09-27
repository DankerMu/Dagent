"""A local provider callback must preserve redelivery and task ownership."""

import pytest

from tests.web.services import test_trigger_provider_pipeline as fixtures
from xagent.web.models.task import Task, TaskStatus
from xagent.web.models.trigger import TriggerAuditOutcome, TriggerRun
from xagent.web.models.user import User
from xagent.web.services.trigger_providers import process_trigger_callback
from xagent.web.services.triggers import (
    _load_prepared_trigger_start,
    prepare_trigger_run,
)

from .test_trigger_provider_pipeline import (
    _audits,
    _context,
    _create_agent,
    _create_stub_trigger,
    _create_user,
    _event_body,
    _good_headers,
)

db_session = fixtures.db_session
stub_provider = fixtures.stub_provider
_fast_run_start = fixtures._fast_run_start


@pytest.mark.asyncio
async def test_transient_provider_ingest_failure_requests_redelivery_and_audits(
    db_session, stub_provider, monkeypatch
):
    user = _create_user(db_session)
    agent = _create_agent(db_session, user)
    _create_stub_trigger(db_session, user, agent)

    async def transient_parse(*args, **kwargs):
        raise ConnectionError("local callback source temporarily unavailable")

    monkeypatch.setattr(stub_provider, "parse_events", transient_parse)
    result = await process_trigger_callback(
        db_session,
        context=_context(headers=_good_headers()),
        raw_body=_event_body(),
    )

    assert result.status_code == stub_provider.ack_policy.failure_status
    assert result.outcome == TriggerAuditOutcome.EXECUTION_FAILURE
    assert db_session.query(TriggerRun).count() == 0
    assert [
        (audit.outcome, audit.detail["stage"]) for audit in _audits(db_session)
    ] == [("execution_failure", "ingest")]


@pytest.mark.asyncio
async def test_provider_finalization_failure_requests_redelivery_without_false_ack(
    db_session, stub_provider, monkeypatch
):
    user = _create_user(db_session)
    agent = _create_agent(db_session, user)
    _create_stub_trigger(db_session, user, agent)

    async def failed_finalize(**kwargs):
        raise ConnectionError("provider cursor unavailable")

    monkeypatch.setattr(stub_provider, "finalize_callback", failed_finalize)
    result = await process_trigger_callback(
        db_session,
        context=_context(headers=_good_headers()),
        raw_body=_event_body(),
    )

    assert result.status_code == stub_provider.ack_policy.failure_status
    assert result.outcome == TriggerAuditOutcome.EXECUTION_FAILURE
    assert db_session.query(TriggerRun).count() == 1
    assert [
        (audit.outcome, audit.detail["stage"]) for audit in _audits(db_session)
    ] == [("execution_failure", "finalize")]


@pytest.mark.parametrize(
    ("task_status", "run_status"),
    [
        ("running", "running"),
        ("completed", "completed"),
        ("failed", "failed"),
    ],
)
def test_prepared_trigger_run_reconciles_task_terminal_state(
    db_session, stub_provider, task_status, run_status
):
    user = _create_user(db_session)
    agent = _create_agent(db_session, user)
    trigger = _create_stub_trigger(db_session, user, agent)
    run, created = prepare_trigger_run(
        db_session,
        trigger=trigger,
        event_payload={"subject": "local task"},
        source_event_id=f"event-{task_status}",
    )
    assert created
    task = db_session.query(Task).filter(Task.id == run.task_id).one()
    task.status = TaskStatus(task_status)
    if task_status == "failed":
        task.error_message = "worker failed"
    db_session.commit()

    assert _load_prepared_trigger_start(int(run.id)) is None
    db_session.refresh(run)
    assert run.status == run_status
    if task_status == "failed":
        assert run.error_message == "worker failed"
    else:
        assert run.error_message is None


def test_prepared_trigger_uses_task_owner_not_webhook_creator(
    db_session, stub_provider
):
    creator = _create_user(db_session)
    agent = _create_agent(db_session, creator)
    trigger = _create_stub_trigger(db_session, creator, agent)
    run, _ = prepare_trigger_run(
        db_session,
        trigger=trigger,
        event_payload={"subject": "shared task"},
        source_event_id="event-shared-task",
    )
    task = db_session.query(Task).filter(Task.id == run.task_id).one()
    owner = User(username="task-owner", password_hash="hash", is_admin=False)
    db_session.add(owner)
    db_session.flush()
    task.user_id = owner.id
    db_session.commit()

    prepared = _load_prepared_trigger_start(int(run.id))
    assert prepared is not None
    assert prepared.task_owner_user_id == owner.id
    assert prepared.task_owner_user_id != creator.id
