"""Authenticated local skill listing and authoring; no public registry surface."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from xagent.skills.library import (
    SkillScopeContext,
    SkillWriteContext,
    get_skill_write_provider,
)
from xagent.skills.parser import SkillParser
from xagent.web.auth_dependencies import get_current_user
from xagent.web.models.database import get_db
from xagent.web.models.user import User
from xagent.web.services.skill_runtime import (
    get_skill_runtime_scope,
    handoff_skill_runtime_session,
    invoke_skill_write_provider,
)

from .local_skill_bundle import (
    MAX_BUNDLE_BYTES,
    MAX_SKILL_MD_CHARS,
    derive_upload_name,
    extract_skill_zip,
    normalize_skill_files,
)
from .local_skill_store import (
    delete_personal_skill,
    personal_upload_matches,
    update_personal_skill_md,
    write_personal_skill,
)

router = APIRouter()


class SkillSummary(BaseModel):
    name: str
    description: str = ""
    when_to_use: str = ""
    tags: list[str] = Field(default_factory=list)
    source: str
    scope: str | None = None
    effective: bool = True
    shadowed_by: str | None = None


class InstalledSkillDetail(SkillSummary):
    content: str = ""
    execution_flow: str = ""
    files: list[str] = Field(default_factory=list)
    path: str


class CreateSkillRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    skill_md: str = Field(..., min_length=1, max_length=MAX_SKILL_MD_CHARS)
    scope: str = Field("personal", pattern="^(personal|team)$")


class EditSkillRequest(BaseModel):
    skill_md: str = Field(..., min_length=1, max_length=MAX_SKILL_MD_CHARS)


async def request_skill_manager(context: SkillScopeContext, db: Session) -> Any:
    """Fresh scoped view includes the current user's DB records and any team overlay."""
    from xagent.skills.utils import create_skill_manager

    handoff_skill_runtime_session(db)
    manager = create_skill_manager(context=context)
    await manager.ensure_initialized()
    return manager


def _source(skill: dict[str, Any]) -> str:
    scope = skill.get("scope")
    if scope == "personal":
        return "user"
    if isinstance(scope, str) and scope:
        return scope
    return skill.get("source") or "external"


def _summary(skill: dict[str, Any]) -> SkillSummary:
    return SkillSummary(
        name=skill["name"],
        description=skill.get("description", ""),
        when_to_use=skill.get("when_to_use", ""),
        tags=skill.get("tags", []),
        source=_source(skill),
        scope=skill.get("scope"),
        effective=bool(skill.get("effective", True)),
        shadowed_by=skill.get("shadowed_by"),
    )


def _personal_summary(name: str, files: dict[str, bytes]) -> SkillSummary:
    parsed = SkillParser.parse_bundle(name=name, files=files)
    parsed["scope"] = "personal"
    return _summary(parsed)


def _write_context(context: SkillScopeContext) -> SkillWriteContext:
    return SkillWriteContext(user_id=context.user_id, metadata=dict(context.metadata))


async def _team_readback(
    name: str, context: SkillScopeContext, db: Session
) -> SkillSummary:
    manager = await request_skill_manager(context, db)
    skill = await manager.get_skill(name)
    if skill is None or _source(skill) != "team":
        raise HTTPException(
            status_code=500,
            detail=f"Skill {name!r} was written to the team scope but the provider does not serve it back.",
        )
    return _summary(skill)


@router.get("/installed", response_model=list[SkillSummary])
async def list_installed(
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
) -> list[SkillSummary]:
    manager = await request_skill_manager(context, db)
    summaries = [_summary(skill) for skill in manager._skills_cache.values()]
    return sorted(
        summaries, key=lambda skill: (skill.source != "user", skill.name.lower())
    )


@router.get("/installed/{name}", response_model=InstalledSkillDetail)
async def get_installed(
    name: str,
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
) -> InstalledSkillDetail:
    manager = await request_skill_manager(context, db)
    skill = await manager.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    return InstalledSkillDetail(
        **_summary(skill).model_dump(),
        content=skill.get("content", ""),
        execution_flow=skill.get("execution_flow", ""),
        files=skill.get("files", []),
        path=skill.get("path", ""),
    )


@router.post("/create", response_model=SkillSummary)
async def create_skill(
    body: CreateSkillRequest,
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillSummary:
    files = {"SKILL.md": body.skill_md.encode("utf-8")}
    if body.scope == "personal":
        saved = write_personal_skill(
            db=db, user_id=int(user.id), name=body.name, files=files
        )
        return _personal_summary(body.name, saved)
    files = normalize_skill_files(files)
    await invoke_skill_write_provider(
        get_skill_write_provider(),
        "create_skill",
        _write_context(context),
        scope="team",
        name=body.name,
        files=files,
    )
    return await _team_readback(body.name, context, db)


@router.post("/upload", response_model=SkillSummary)
async def upload_skill(
    file: UploadFile = File(...),
    scope: str = Form("personal"),
    name: str | None = Form(None),
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillSummary:
    if scope not in ("personal", "team"):
        raise HTTPException(
            status_code=400, detail="scope must be 'personal' or 'team'."
        )
    from xagent.config import get_max_upload_size_bytes

    limit = min(get_max_upload_size_bytes(), MAX_BUNDLE_BYTES)
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {limit // (1024 * 1024)} MiB limit.",
        )
    filename = (file.filename or "").strip()
    if filename.lower().endswith(".zip"):
        files, root = await asyncio.to_thread(extract_skill_zip, data, max_bytes=limit)
    elif filename.lower().endswith(".md"):
        files, root = {"SKILL.md": data}, ""
    else:
        raise HTTPException(
            status_code=400,
            detail="Unsupported upload — provide a .zip skill bundle or a SKILL.md file.",
        )
    files = normalize_skill_files(files, max_bytes=limit)
    skill_name = derive_upload_name(filename, root, files["SKILL.md"], override=name)
    if scope == "personal":
        try:
            saved = write_personal_skill(
                db=db,
                user_id=int(user.id),
                name=skill_name,
                files=files,
                origin="upload",
            )
        except HTTPException as exc:
            if exc.status_code != 409 or not personal_upload_matches(
                db=db, user_id=int(user.id), name=skill_name, files=files
            ):
                raise
            saved = files
        return _personal_summary(skill_name, saved)
    await invoke_skill_write_provider(
        get_skill_write_provider(),
        "create_skill",
        _write_context(context),
        scope="team",
        name=skill_name,
        files=files,
        origin="upload",
    )
    return await _team_readback(skill_name, context, db)


@router.put("/installed/{name}", response_model=SkillSummary)
async def edit_installed(
    name: str,
    body: EditSkillRequest,
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> SkillSummary:
    manager = await request_skill_manager(context, db)
    skill = await manager.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    source = _source(skill)
    if source == "team":
        content = body.skill_md.encode("utf-8")
        normalize_skill_files({"SKILL.md": content})
        await invoke_skill_write_provider(
            get_skill_write_provider(),
            "update_skill_file",
            _write_context(context),
            scope="team",
            name=name,
            path="SKILL.md",
            content=content,
        )
        return await _team_readback(name, context, db)
    if source != "user":
        raise HTTPException(
            status_code=403, detail="Only user-installed skills can be edited."
        )
    files = update_personal_skill_md(
        db=db, user_id=int(user.id), name=name, skill_md=body.skill_md
    )
    return _personal_summary(name, files)


@router.delete(
    "/installed/{name}", status_code=status.HTTP_204_NO_CONTENT, response_class=Response
)
async def delete_installed(
    name: str,
    context: SkillScopeContext = Depends(get_skill_runtime_scope),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> Response:
    manager = await request_skill_manager(context, db)
    skill = await manager.get_skill(name)
    if skill is None:
        raise HTTPException(status_code=404, detail="Skill not found")
    source = _source(skill)
    if source == "team":
        await invoke_skill_write_provider(
            get_skill_write_provider(),
            "delete_skill",
            _write_context(context),
            scope="team",
            name=name,
        )
    elif source == "user":
        delete_personal_skill(db=db, user_id=int(user.id), name=name)
    else:
        raise HTTPException(
            status_code=403,
            detail=f"Cannot delete a {source} skill — only user-installed skills can be removed.",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
