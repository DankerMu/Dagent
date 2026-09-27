"""A delayed first turn must retain server-selected upload attachments."""

from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import sessionmaker

from tests.web import test_websocket_uploaded_files_context as file_fixtures
from xagent.web.services import task_command_execution as execution

# Reuse the existing isolated SQLite/upload fixtures, not a business database.
db_session = file_fixtures.db_session


def test_pending_first_turn_recovers_old_selected_upload_without_wire_files(
    db_session, tmp_path, monkeypatch
):
    monkeypatch.setenv("XAGENT_UPLOADS_DIR", str(tmp_path))
    owner = file_fixtures._create_user(db_session, 1, "delayed-upload-owner")
    task = file_fixtures._create_task(
        db_session, task_id=10, user_id=int(owner.id), selected_file_ids=["selected"]
    )
    upload = file_fixtures._create_uploaded_file(
        db_session,
        tmp_path,
        file_id="selected",
        user_id=int(owner.id),
        task_id=int(task.id),
        filename="delayed.txt",
    )
    # Outside the recent-upload race fallback: only persisted selection can recover it.
    upload.created_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
        hours=1
    )
    db_session.commit()
    monkeypatch.setattr(
        execution, "get_session_local", lambda: sessionmaker(bind=db_session.get_bind())
    )

    preparation = execution._prepare_task_message_sync(
        requested_task_id=int(task.id),
        actor_user_id=int(owner.id),
        actor_is_admin=False,
        user_message="Use the document I selected earlier",
        raw_context={},
        raw_files=[],
        client_message_id="delayed-first-turn",
        turn_id="delayed-first-turn",
        durable_attempt_count=1,
        durable_target_run_id=None,
        pause_accepted=False,
    )

    assert preparation.turn_payload.file_ids == ("selected",)
    assert preparation.display_file_refs == (
        {
            "file_id": "selected",
            "name": "delayed.txt",
            "size": 12,
            "type": "text/plain",
        },
    )
    assert "delayed.txt" in preparation.user_message_for_llm
