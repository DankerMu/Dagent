"""Historic sender stamps remain authorization fences for old channel tasks."""

import pytest

from tests.web.services.test_channel_runtime import _create_channel_session_local
from xagent.web.models import database as database_module
from xagent.web.models.task import Task, TaskStatus
from xagent.web.models.user_channel import UserChannel
from xagent.web.services import channel_runtime


@pytest.mark.asyncio
async def test_legacy_channel_task_cannot_be_resumed_by_another_authorized_sender(
    tmp_path, monkeypatch, mock_workspace_db
):
    del mock_workspace_db
    engine, sessions, owner_id, channel_id = _create_channel_session_local(tmp_path)
    monkeypatch.setattr(channel_runtime, "get_session_local", lambda: sessions)
    monkeypatch.setattr(database_module, "get_session_local", lambda: sessions)
    try:
        with sessions() as db:
            channel = db.get(UserChannel, channel_id)
            channel.config = {"allowed_users": ["telegram-user", "other-sender"]}
            old_task = Task(
                user_id=owner_id,
                title="Other sender's history",
                channel_id=channel_id,
                status=TaskStatus.COMPLETED,
                telegram_user_id="telegram-user",
            )
            db.add(old_task)
            db.commit()
            old_task_id = old_task.id

        prepared = await channel_runtime.prepare_channel_task(
            channel_id=channel_id,
            external_user_id="other-sender",
            active_task_id=old_task_id,
            text="continue",
            channel_name="Local channel",
        )
        assert prepared is not None
        assert prepared.task_id != old_task_id
        assert prepared.is_new_task
        with sessions() as db:
            assert db.get(Task, old_task_id).status == TaskStatus.COMPLETED
            assert db.get(Task, old_task_id).telegram_user_id == "telegram-user"
        assert await prepared.managed_lease.finalize_result(status=TaskStatus.COMPLETED)
    finally:
        engine.dispose()
