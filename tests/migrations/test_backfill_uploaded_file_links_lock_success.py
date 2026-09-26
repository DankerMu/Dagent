"""Successful uploaded-file-link backfill returns its result and releases locks."""

from __future__ import annotations

import tempfile
from pathlib import Path

import lancedb
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.core.tools.core.RAG_tools.LanceDB.schema_manager import (
    ensure_documents_table,
)
from xagent.migrations.lancedb import backfill_uploaded_file_links
from xagent.web.models.database import Base
from xagent.web.models.uploaded_file import UploadedFile
from xagent.web.models.user import User


@pytest.fixture
def isolated_migration_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    with tempfile.TemporaryDirectory() as temp_dir:
        conn = lancedb.connect(temp_dir)
        ensure_documents_table(conn)
        docs_table = conn.open_table("documents")

        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        SessionLocal = sessionmaker(bind=engine)

        monkeypatch.setattr(
            backfill_uploaded_file_links,
            "get_session_local",
            lambda: SessionLocal,
        )
        monkeypatch.setenv(
            "LANCEDB_MIGRATION_LOCK_FILE",
            str(tmp_path / "uploaded-file-links.lock"),
        )
        monkeypatch.setattr(
            backfill_uploaded_file_links,
            "get_connection_from_env",
            lambda: conn,
        )
        try:
            yield conn, docs_table, SessionLocal, Path(temp_dir)
        finally:
            engine.dispose()


def test_backfill_all_returns_result_and_releases_lock(isolated_migration_env):
    """Happy-path backfill_all must return the migration stats, not a lock error,
    and drop both the in-process lock and the file lock so a later run can start.

    Existing tests only cover the raise-and-release path, so CI never executes
    the success return under lock. The lock file is isolated to tmp so parallel
    suites cannot contend on the default shared path.
    """
    conn, docs_table, SessionLocal, base_dir = isolated_migration_env

    source_file = Path(base_dir) / "user_1" / "kb" / "page.md"
    source_file.parent.mkdir(parents=True, exist_ok=True)
    source_file.write_text("content", encoding="utf-8")
    docs_table.add(
        [
            {
                "collection": "kb",
                "doc_id": "doc-1",
                "file_id": None,
                "source_path": str(source_file),
                "user_id": 1,
            }
        ]
    )

    db = SessionLocal()
    db.add(
        User(
            id=1,
            username="user_1",
            password_hash="hash",  # pragma: allowlist secret - synthetic fixture
            is_admin=False,
        )
    )
    db.commit()
    db.add(
        UploadedFile(
            user_id=1,
            filename=source_file.name,
            storage_path=str(source_file),
            mime_type="text/markdown",
            file_size=source_file.stat().st_size,
        )
    )
    db.commit()
    db.close()

    result = backfill_uploaded_file_links.backfill_all(dry_run=False, batch_size=10)

    assert "error" not in result
    assert result["backfilled_by_match"] == 1
    assert result["failures"] == 0

    # A second invocation must acquire both locks and observe the persisted link.
    repeated = backfill_uploaded_file_links.backfill_all(dry_run=False, batch_size=10)
    assert "error" not in repeated
    assert repeated["backfilled_by_match"] == 0
