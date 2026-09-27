"""Isolated owner-scoped skill tables for local authoring tests."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from xagent.web.models.database import Base
from xagent.web.models.skill import UserSkill, UserSkillFile
from xagent.web.models.user import User


@pytest.fixture
def skill_db_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'skills.db'}")
    Base.metadata.create_all(
        engine, tables=[User.__table__, UserSkill.__table__, UserSkillFile.__table__]
    )
    factory = sessionmaker(bind=engine)
    with factory() as db:
        for uid in (7, 8):
            db.execute(
                User.__table__.insert().values(
                    id=uid, username=f"u{uid}", password_hash="h", is_admin=False
                )
            )
        db.commit()
    yield factory
    engine.dispose()
