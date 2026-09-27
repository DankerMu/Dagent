"""Durable ingress metadata cannot be overwritten by delivery replays."""

from tests.web.services import test_channel_input_acceptance as fixtures
from xagent.web.models.task import Task

engine = fixtures.engine
ingress = fixtures.ingress


def test_accepted_task_metadata_survives_duplicate_delivery_with_changed_overrides(
    ingress,
):
    incoming, sessions = ingress
    first = fixtures.accept(
        incoming,
        task_title="Original document",
        task_description="Original request",
    )
    with sessions() as db:
        task = db.get(Task, first.task_id)
        assert task.title == "Original document"
        assert task.description == "Original request"

    replay = fixtures.accept(
        incoming,
        task_title="Untrusted duplicate title",
        task_description="Untrusted duplicate description",
    )

    assert replay.replayed
    assert replay.task_id == first.task_id
    with sessions() as db:
        task = db.get(Task, first.task_id)
        assert task.title == "Original document"
        assert task.description == "Original request"
