"""Durable progress retains earlier step observations across updates."""

from tests.web.test_background_jobs import _create_user, _init_test_db
from xagent.config import CELERY_BROKER_URL, CELERY_ENABLED
from xagent.web.models import database
from xagent.web.models.background_job import BackgroundJobType
from xagent.web.services.background_jobs import create_background_job


def test_background_job_progress_manager_mirrors_rag_progress(tmp_path, monkeypatch):
    monkeypatch.setenv(CELERY_ENABLED, "false")
    monkeypatch.delenv(CELERY_BROKER_URL, raising=False)

    from xagent.web.jobs.progress import BackgroundJobProgressManager

    class Delegate:
        def create_task(self, **kwargs):
            return kwargs["task_id"]

        def update_task_progress(self, *args, **kwargs):
            return None

        def complete_task(self, *args, **kwargs):
            return None

        def track_task(self, *args, **kwargs):
            raise AssertionError("not used")

        def get_active_tasks(self, *args, **kwargs):
            return []

    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    SessionLocal = _init_test_db(tmp_path / "jobs-progress.db")
    db = SessionLocal()
    try:
        user = _create_user(db, username="progress-test")
        job = create_background_job(
            db,
            user_id=int(user.id),
            job_type=BackgroundJobType.KB_INGEST_DOCUMENT,
            payload={"collection": "kb"},
        )

        manager = BackgroundJobProgressManager(
            db,
            job,
            delegate=Delegate(),
            throttle_seconds=0,
        )
        task_id = manager.create_task("ingestion", task_id="task-1")
        manager.update_task_progress(
            task_id,
            current_step="parse_document",
            overall_progress=0.25,
            metadata={
                "steps": {
                    "parse_document": {
                        "message": "Parsing document",
                        "step_progress": 0.5,
                    }
                }
            },
        )

        db.refresh(job)
        assert job.progress["message"] == "Parsing document"
        assert job.progress["completed"] == 25
        assert job.progress["total"] == 100
        assert job.progress["current_step"] == "parse_document"
        assert (
            job.progress["metadata"]["steps"]["parse_document"]["step_progress"] == 0.5
        )

        manager.update_task_progress(
            task_id,
            current_step="chunk_document",
            overall_progress=0.5,
            metadata={
                "steps": {
                    "chunk_document": {
                        "message": "Chunking document",
                        "step_progress": 0.25,
                    }
                }
            },
        )
        db.refresh(job)
        steps = job.progress["metadata"]["steps"]
        assert steps["parse_document"]["step_progress"] == 0.5
        assert steps["chunk_document"]["step_progress"] == 0.25
    finally:
        db.close()
        database.get_engine().dispose()
