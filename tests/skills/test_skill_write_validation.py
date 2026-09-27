"""Personal writes validate the stored bundle before the transaction commits."""

from __future__ import annotations

import threading

import pytest
from fastapi import HTTPException

from xagent.web.api.local_skill_bundle import normalize_skill_files
from xagent.web.api.local_skill_store import (
    _is_skill_name_unique_violation,
    delete_personal_skill,
    update_personal_skill_md,
    write_personal_skill,
)
from xagent.web.models.skill import UserSkill, UserSkillFile

SKILL_MD = b"# Test Skill\n\n## Description\nA test skill.\n"


@pytest.fixture
def db(skill_db_factory):
    with skill_db_factory() as session:
        yield session


@pytest.mark.parametrize(
    "files",
    [
        {"SKILL.md": b"\xe9\xff\xfe latin-1"},
        {"SKILL.md": SKILL_MD, "template.md": b"\xff\xfe not utf8"},
        {"SKILL.md": SKILL_MD, "/template.md": b"\xff\xfe"},
    ],
)
def test_unloadable_normalized_bundle_never_reaches_table(db, files):
    with pytest.raises(HTTPException) as error:
        write_personal_skill(db=db, user_id=7, name="bad", files=files)
    assert error.value.status_code == 400
    assert db.query(UserSkill).count() == 0
    write_personal_skill(db=db, user_id=7, name="bad", files={"SKILL.md": SKILL_MD})
    assert db.query(UserSkill).count() == 1


def test_parser_fault_is_not_misreported_as_bad_input(db, monkeypatch):
    from xagent.skills.parser import SkillParser

    def broken_parser(*args, **kwargs):
        raise RuntimeError("parser defect")

    monkeypatch.setattr(SkillParser, "parse_bundle", broken_parser)
    with pytest.raises(RuntimeError, match="parser defect"):
        write_personal_skill(
            db=db, user_id=7, name="alpha", files={"SKILL.md": SKILL_MD}
        )
    assert db.query(UserSkill).count() == 0


def test_normalization_drops_archive_cruft_not_good_assets(db):
    write_personal_skill(
        db=db,
        user_id=7,
        name="alpha",
        files={"SKILL.md": SKILL_MD, ".DS_Store": b"\xff", "logo.png": b"\x89PNG\xff"},
    )
    assert {file.path for file in db.query(UserSkillFile).all()} == {
        "SKILL.md",
        "logo.png",
    }


def test_edit_refuses_invalid_body_before_publication_and_preserves_assets(db):
    write_personal_skill(
        db=db,
        user_id=7,
        name="alpha",
        files={"SKILL.md": SKILL_MD, "template.md": b"reference"},
    )
    with pytest.raises(HTTPException) as error:
        update_personal_skill_md(
            db=db, user_id=7, name="alpha", skill_md="\ufeff\u200b\t"
        )
    assert error.value.status_code == 400
    files = {file.path: bytes(file.content) for file in db.query(UserSkillFile).all()}
    assert files == {"SKILL.md": SKILL_MD, "template.md": b"reference"}


def test_owner_and_name_are_both_required_for_update_and_delete(db):
    for name in ("alpha", "beta"):
        write_personal_skill(db=db, user_id=8, name=name, files={"SKILL.md": SKILL_MD})
    with pytest.raises(HTTPException) as edit:
        update_personal_skill_md(db=db, user_id=7, name="beta", skill_md="# changed")
    with pytest.raises(HTTPException) as delete:
        delete_personal_skill(db=db, user_id=7, name="beta")
    assert edit.value.status_code == delete.value.status_code == 404
    delete_personal_skill(db=db, user_id=8, name="beta")
    assert [skill.name for skill in db.query(UserSkill).all()] == ["alpha"]


def test_only_name_unique_constraint_translates_to_conflict():
    from sqlalchemy.exc import IntegrityError

    def error(message):
        return IntegrityError("INSERT", {}, Exception(message))

    assert _is_skill_name_unique_violation(error("uq_user_skill_name violated"))
    assert _is_skill_name_unique_violation(
        error("UNIQUE constraint failed: user_skills.user_id, user_skills.name")
    )
    assert not _is_skill_name_unique_violation(error("FOREIGN KEY constraint failed"))
    assert not _is_skill_name_unique_violation(
        error(
            "UNIQUE constraint failed: user_skill_files.skill_id, user_skill_files.path"
        )
    )


def test_normalization_rejects_traversal_hidden_files_and_collisions():
    for path in ("../escape.md", "a/../../escape.md", "sub/.env"):
        with pytest.raises(HTTPException) as error:
            normalize_skill_files({"SKILL.md": SKILL_MD, path: b"x"})
        assert error.value.status_code == 400
    with pytest.raises(HTTPException) as collision:
        normalize_skill_files(
            {"SKILL.md": SKILL_MD, "a\\b.md": b"one", "a/b.md": b"two"}
        )
    assert collision.value.status_code == 400


def test_duplicate_name_race_reports_conflict_without_orphaned_files(skill_db_factory):
    """Both attempts must finish their empty precheck before either inserts."""
    barrier = threading.Barrier(2, timeout=30)
    outcomes: list[str] = []
    reached: list[int] = []
    lock = threading.Lock()

    def attempt() -> None:
        with skill_db_factory() as session:
            original_query = session.query
            synchronized = False

            def query_then_sync(*args, **kwargs):
                nonlocal synchronized
                result = original_query(*args, **kwargs)
                if synchronized:
                    return result
                synchronized = True

                class Checked:
                    def filter(self, *args, **kwargs):
                        self.filtered = result.filter(*args, **kwargs)
                        return self

                    def first(self):
                        row = self.filtered.first()
                        with lock:
                            reached.append(1)
                        barrier.wait()
                        return row

                return Checked()

            session.query = query_then_sync
            try:
                write_personal_skill(
                    db=session, user_id=7, name="raced", files={"SKILL.md": SKILL_MD}
                )
                with lock:
                    outcomes.append("committed")
            except HTTPException as error:
                with lock:
                    outcomes.append(f"http-{error.status_code}")
            except BaseException as error:
                with lock:
                    outcomes.append(f"raised-{type(error).__name__}: {error}")

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert len(reached) == 2
    assert sorted(outcomes) == ["committed", "http-409"]
    with skill_db_factory() as session:
        assert session.query(UserSkill).count() == 1
        assert session.query(UserSkillFile).count() == 1
