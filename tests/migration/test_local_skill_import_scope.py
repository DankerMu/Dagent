"""Skill migration keeps local bundles isolated to their importing owner."""

import pytest

from xagent.migration.bundle import MigrationBundle, SkillItem
from xagent.migration.loaders import MigrationLoader
from xagent.web.models.database import Base, get_engine, get_session_local, init_db
from xagent.web.models.skill import UserSkill, UserSkillFile
from xagent.web.models.user import User


@pytest.fixture
def db_session(tmp_path):
    init_db(db_url=f"sqlite:///{tmp_path / 'skill-migration.db'}")
    session = get_session_local()()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=get_engine())


def test_import_rejects_escaping_path_and_imports_next_skill_for_same_user(db_session):
    owner = User(username="local-owner", password_hash="hash", is_admin=False)
    other = User(username="other-owner", password_hash="hash", is_admin=False)
    db_session.add_all([owner, other])
    db_session.commit()
    db_session.refresh(owner)
    db_session.refresh(other)
    files = {"SKILL.md": b"## Description\nLocal only\n"}
    bundle = MigrationBundle(source="hermes", source_root="synthetic")
    bundle.skills = [
        SkillItem(
            name="unsafe",
            source_path="synthetic/unsafe",
            files={**files, "../outside.txt": b"private"},
        ),
        SkillItem(name="safe", source_path="synthetic/safe", files=files),
    ]

    report = MigrationLoader(db_session, user=owner).load(bundle)

    assert report.skills_imported == ["safe"]
    assert len(report.errors) == 1 and "unsafe" in report.errors[0]
    assert (
        db_session.query(UserSkill).filter(UserSkill.user_id == other.id).count() == 0
    )
    skill = db_session.query(UserSkill).filter(UserSkill.user_id == owner.id).one()
    assert skill.name == "safe"
    assert [
        (row.path, row.content) for row in db_session.query(UserSkillFile).all()
    ] == [("SKILL.md", files["SKILL.md"])]


def test_reimport_rename_preserves_existing_and_advances_to_unused_name(db_session):
    owner = User(username="renamed-owner", password_hash="hash", is_admin=False)
    db_session.add(owner)
    db_session.commit()
    db_session.refresh(owner)
    bundle = MigrationBundle(source="hermes", source_root="synthetic")
    bundle.skills = [
        SkillItem(
            name="review",
            source_path="synthetic/review",
            files={"SKILL.md": b"## Description\nReview staged changes\n"},
        )
    ]

    assert MigrationLoader(db_session, user=owner).load(bundle).skills_imported == [
        "review"
    ]
    assert MigrationLoader(db_session, user=owner, skill_conflict="rename").load(
        bundle
    ).skills_imported == ["review-imported"]
    assert MigrationLoader(db_session, user=owner, skill_conflict="rename").load(
        bundle
    ).skills_imported == ["review-imported-2"]
    assert {
        row.name
        for row in db_session.query(UserSkill).filter(UserSkill.user_id == owner.id)
    } == {"review", "review-imported", "review-imported-2"}
