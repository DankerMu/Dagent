"""Only output files belonging to the authorized task are deliverable."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.task import Task
from xagent.web.models.uploaded_file import UploadedFile
from xagent.web.models.user import User
from xagent.web.services import channel_runtime


@pytest.mark.asyncio
async def test_channel_output_preserves_order_without_exposing_other_tasks_files(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{tmp_path / 'output-scope.db'}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    monkeypatch.setattr(channel_runtime, "get_session_local", lambda: sessions)
    try:
        with sessions() as db:
            owner = User(username="file-owner", password_hash="unused")
            outsider = User(username="file-outsider", password_hash="unused")
            db.add_all((owner, outsider))
            db.flush()
            task = Task(user_id=owner.id, title="Current task")
            other_task = Task(user_id=owner.id, title="Older task")
            db.add_all((task, other_task))
            db.flush()
            db.add_all(
                (
                    UploadedFile(
                        file_id="first",
                        user_id=owner.id,
                        task_id=task.id,
                        filename="one.txt",
                        storage_path="/one",
                    ),
                    UploadedFile(
                        file_id="second",
                        user_id=owner.id,
                        task_id=task.id,
                        filename="two.txt",
                        storage_path="/two",
                    ),
                    UploadedFile(
                        file_id="other-task",
                        user_id=owner.id,
                        task_id=other_task.id,
                        filename="old.txt",
                        storage_path="/old",
                    ),
                    UploadedFile(
                        file_id="other-user",
                        user_id=outsider.id,
                        task_id=task.id,
                        filename="secret.txt",
                        storage_path="/secret",
                    ),
                )
            )
            db.commit()
            task_id, owner_id = task.id, owner.id

        files = await channel_runtime.load_channel_output_files(
            file_ids=("second", "other-task", "first", "other-user", "second"),
            task_id=task_id,
            user_id=owner_id,
        )

        assert [(file.file_id, file.filename, file.storage_path) for file in files] == [
            ("second", "two.txt", "/two"),
            ("first", "one.txt", "/one"),
        ]
    finally:
        engine.dispose()
