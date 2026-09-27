"""Channel task metadata updates are fenced by the persisted task owner."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.task import Task
from xagent.web.models.user import User
from xagent.web.services import channel_runtime


@pytest.mark.asyncio
async def test_channel_task_metadata_update_rejects_other_owner_and_preserves_unspecified_fields(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{tmp_path / 'metadata-scope.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(channel_runtime, "get_session_local", lambda: sessions)
    try:
        with sessions() as db:
            owner = User(username="metadata-owner", password_hash="unused")
            stranger = User(username="metadata-stranger", password_hash="unused")
            db.add_all([owner, stranger])
            db.flush()
            task = Task(
                user_id=owner.id, title="Original", description="Keep description"
            )
            db.add(task)
            db.commit()
            task_id, owner_id, stranger_id = task.id, owner.id, stranger.id

        await channel_runtime.update_channel_task_fields(
            task_id=task_id,
            user_id=stranger_id,
            title="Unauthorized",
            description="Stolen",
        )
        with sessions() as db:
            task = db.get(Task, task_id)
            assert (task.title, task.description) == ("Original", "Keep description")

        await channel_runtime.update_channel_task_fields(
            task_id=task_id,
            user_id=owner_id,
            title="Renamed",
        )
        with sessions() as db:
            task = db.get(Task, task_id)
            assert (task.title, task.description) == ("Renamed", "Keep description")
    finally:
        engine.dispose()
