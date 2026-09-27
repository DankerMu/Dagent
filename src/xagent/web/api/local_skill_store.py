"""Owner-scoped transactions for personal database-backed skills."""

from __future__ import annotations

import hashlib
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from xagent.skills.library import guess_media_type
from xagent.web.models.skill import UserSkill, UserSkillFile

from .local_skill_bundle import normalize_skill_files, validate_skill_name


def _is_skill_name_unique_violation(error: BaseException) -> bool:
    """Only translate the (user_id, name) constraint, never unrelated DB defects."""
    current: BaseException | None = error
    seen: set[int] = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        message = str(current).lower()
        if "uq_user_skill_name" in message:
            return True
        if (
            "user_skills.user_id" in message
            and "user_skills.name" in message
            and ("unique" in message or "duplicate" in message)
        ):
            return True
        current = current.__cause__ or current.__context__
    return False


def _owner_skill(db: Any, user_id: int, name: str) -> UserSkill:
    skill = (
        db.query(UserSkill)
        .filter(UserSkill.user_id == user_id, UserSkill.name == name)
        .first()
    )
    if skill is None:
        raise HTTPException(status_code=404, detail="Personal skill not found")
    return cast(UserSkill, skill)


def _set_file(file: UserSkillFile, content: bytes) -> None:
    setattr(file, "content", content)
    setattr(file, "size_bytes", len(content))
    setattr(file, "sha256", hashlib.sha256(content).hexdigest())
    setattr(file, "media_type", guess_media_type(cast(str, file.path)))


def write_personal_skill(
    *, db: Any, user_id: int, name: str, files: dict[str, bytes], origin: str = "custom"
) -> dict[str, bytes]:
    """Validate first, then atomically publish a unique owner/name bundle."""
    validate_skill_name(name)
    normalized = normalize_skill_files(files)
    existing = (
        db.query(UserSkill)
        .filter(UserSkill.user_id == user_id, UserSkill.name == name)
        .first()
    )
    if existing is not None:
        raise HTTPException(
            status_code=409, detail=f"A personal skill named {name!r} already exists."
        )
    skill = UserSkill(
        user_id=user_id,
        name=name,
        origin=origin,
        created_by_user_id=user_id,
        updated_by_user_id=user_id,
    )
    db.add(skill)
    try:
        db.flush()
        skill_id = int(skill.id)
        for path, content in sorted(normalized.items()):
            file = UserSkillFile(skill_id=skill_id, path=path)
            _set_file(file, content)
            db.add(file)
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        if not _is_skill_name_unique_violation(exc):
            raise
        raise HTTPException(
            status_code=409, detail=f"A personal skill named {name!r} already exists."
        ) from exc
    return normalized


def update_personal_skill_md(
    *, db: Any, user_id: int, name: str, skill_md: str
) -> dict[str, bytes]:
    skill = _owner_skill(db, user_id, name)
    content = skill_md.encode("utf-8")
    # Validate the *whole* resulting bundle before changing any ORM state.
    files = {item.path: bytes(item.content) for item in skill.files}
    files["SKILL.md"] = content
    normalized = normalize_skill_files(files)
    file = next((item for item in skill.files if item.path == "SKILL.md"), None)
    if file is None:
        file = UserSkillFile(skill_id=skill.id, path="SKILL.md")
        db.add(file)
    _set_file(file, content)
    setattr(skill, "updated_by_user_id", user_id)
    db.commit()
    return normalized


def delete_personal_skill(*, db: Any, user_id: int, name: str) -> None:
    skill = _owner_skill(db, user_id, name)
    db.delete(skill)
    db.commit()


def personal_upload_matches(
    *, db: Any, user_id: int, name: str, files: dict[str, bytes]
) -> bool:
    """An identical replay of a personal upload can return its original result."""
    from sqlalchemy.orm import selectinload

    skill = (
        db.query(UserSkill)
        .options(selectinload(UserSkill.files))
        .filter(
            UserSkill.user_id == user_id,
            UserSkill.name == name,
            UserSkill.origin == "upload",
        )
        .first()
    )
    return (
        skill is not None
        and {item.path: bytes(item.content) for item in skill.files} == files
    )
