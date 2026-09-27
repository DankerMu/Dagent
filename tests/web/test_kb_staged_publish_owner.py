"""A staged upload must not overwrite a newer owner's canonical file."""

import pytest

from xagent.web.jobs.kb_tasks import _publish_staged_document_ingestion
from xagent.web.models.database import Base, get_engine, get_session_local, init_db
from xagent.web.models.uploaded_file import UploadedFile
from xagent.web.models.user import User


def test_staged_publish_refuses_canonical_file_replaced_by_another_upload(tmp_path):
    init_db(db_url=f"sqlite:///{tmp_path / 'staged-owner.db'}")
    db = get_session_local()()
    try:
        owner = User(username="staged-file-owner", password_hash="hash", is_admin=False)
        db.add(owner)
        db.commit()
        db.refresh(owner)
        canonical = tmp_path / "files" / "report.txt"
        canonical.parent.mkdir()
        canonical.write_bytes(b"newer version")
        staged = tmp_path / "staged" / "report.txt"
        staged.parent.mkdir()
        staged.write_bytes(b"obsolete version")
        db.add(
            UploadedFile(
                user_id=owner.id,
                file_id="newer-upload",
                filename="report.txt",
                storage_path=str(canonical),
                file_size=len(b"newer version"),
            )
        )
        db.commit()

        with pytest.raises(RuntimeError, match="updated by another upload"):
            _publish_staged_document_ingestion(
                db,
                {
                    "source_path": str(staged),
                    "target_path": str(canonical),
                    "file_id": "outdated-upload",
                    "user_id": owner.id,
                },
            )

        assert canonical.read_bytes() == b"newer version"
        assert staged.read_bytes() == b"obsolete version"
        assert db.query(UploadedFile).one().file_id == "newer-upload"
    finally:
        db.close()
        Base.metadata.drop_all(bind=get_engine())
