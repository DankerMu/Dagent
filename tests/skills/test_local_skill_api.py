"""Local authoring is reachable under the retained skills router and owner-scoped."""

from __future__ import annotations

import io
import zipfile
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from xagent.skills.library import (
    SkillRecord,
    set_skill_library_provider,
    set_skill_write_provider,
)
from xagent.web.api.skills import router
from xagent.web.auth_dependencies import get_current_user
from xagent.web.models.database import get_db
from xagent.web.models.skill import UserSkill, UserSkillFile

SKILL_MD = "# Test Skill\n\n## Description\nA test skill.\n"
UPDATED_MD = "# Test Skill\n\n## Description\nAn updated skill.\n"


def _zip(files: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


@pytest.fixture
def local_api(tmp_path, monkeypatch, skill_db_factory):
    factory = skill_db_factory
    monkeypatch.setattr(
        "xagent.web.models.database.get_optional_session_local", lambda: factory
    )
    monkeypatch.setattr(
        "xagent.skills.utils._get_default_skill_dirs", lambda: [tmp_path / "empty"]
    )
    monkeypatch.setattr("xagent.skills.library._skill_library_provider", None)
    monkeypatch.setattr("xagent.skills.library._skill_write_provider", None)
    actor = SimpleNamespace(id=7)
    app = FastAPI()
    app.include_router(router)

    def current_user():
        return actor

    def db_session():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    app.dependency_overrides[get_current_user] = current_user
    with TestClient(app) as client:
        yield client, actor, factory


def test_personal_create_edit_delete_roundtrip_and_owner_isolation(local_api):
    client, actor, factory = local_api
    created = client.post(
        "/api/skills/create", json={"name": "alpha", "skill_md": SKILL_MD}
    )
    assert created.status_code == 200, created.text
    summary = created.json()
    assert summary["name"] == "alpha"
    assert summary["description"] == "A test skill."
    assert summary["source"] == "user"
    assert summary["scope"] == "personal"
    assert client.get("/api/skills/").status_code == 200
    assert client.get("/api/skills/installed").json()[0]["name"] == "alpha"
    detail = client.get("/api/skills/installed/alpha")
    assert detail.status_code == 200
    assert detail.json()["content"] == SKILL_MD
    assert detail.json()["files"] == ["SKILL.md"]

    actor.id = 8
    assert client.get("/api/skills/installed/alpha").status_code == 404
    assert (
        client.put(
            "/api/skills/installed/alpha", json={"skill_md": UPDATED_MD}
        ).status_code
        == 404
    )
    assert client.delete("/api/skills/installed/alpha").status_code == 404
    actor.id = 7

    changed = client.put("/api/skills/installed/alpha", json={"skill_md": UPDATED_MD})
    assert changed.status_code == 200, changed.text
    assert changed.json()["description"] == "An updated skill."
    assert client.get("/api/skills/installed/alpha").json()["content"] == UPDATED_MD
    assert client.delete("/api/skills/installed/alpha").status_code == 204
    assert client.get("/api/skills/installed/alpha").status_code == 404
    with factory() as db:
        assert db.query(UserSkill).count() == 0
        assert db.query(UserSkillFile).count() == 0


def test_same_name_is_independent_per_owner(local_api):
    client, actor, factory = local_api
    assert (
        client.post(
            "/api/skills/create", json={"name": "shared", "skill_md": SKILL_MD}
        ).status_code
        == 200
    )
    actor.id = 8
    assert (
        client.post(
            "/api/skills/create", json={"name": "shared", "skill_md": UPDATED_MD}
        ).status_code
        == 200
    )
    assert client.get("/api/skills/installed/shared").json()["content"] == UPDATED_MD
    assert client.delete("/api/skills/installed/shared").status_code == 204
    actor.id = 7
    assert client.get("/api/skills/installed/shared").json()["content"] == SKILL_MD
    with factory() as db:
        assert [(skill.user_id, skill.name) for skill in db.query(UserSkill).all()] == [
            (7, "shared")
        ]


def test_scoped_summary_exposes_effective_and_shadow_metadata(local_api):
    client, _, _ = local_api

    class ScopedProvider:
        async def list_records(self, context):
            return [
                SkillRecord(
                    name="team-skill",
                    source="team",
                    scope="team",
                    files={"SKILL.md": SKILL_MD.encode()},
                    effective=False,
                    shadowed_by="personal",
                )
            ]

        async def read_file(self, context, record, path):
            return record.files[path]

    set_skill_library_provider(ScopedProvider())
    try:
        listed = client.get("/api/skills/installed")
        assert listed.status_code == 200
        assert listed.json()[0]["effective"] is False
        assert listed.json()[0]["shadowed_by"] == "personal"
        detail = client.get("/api/skills/installed/team-skill")
        assert detail.json()["scope"] == "team"
        assert detail.json()["effective"] is False
    finally:
        set_skill_library_provider(None)


def test_bundle_upload_preserves_companion_files_on_edit(local_api):
    client, _, factory = local_api
    archive = _zip(
        {
            "bundle/SKILL.md": SKILL_MD.encode(),
            "bundle/notes.md": b"Companion material",
        }
    )
    uploaded = client.post(
        "/api/skills/upload", files={"file": ("bundle.zip", archive, "application/zip")}
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["name"] == "bundle"
    identical_retry = client.post(
        "/api/skills/upload",
        files={"file": ("bundle.zip", archive, "application/zip")},
    )
    assert identical_retry.status_code == 200
    assert identical_retry.json() == uploaded.json()
    assert client.get("/api/skills/installed/bundle").json()["files"] == [
        "SKILL.md",
        "notes.md",
    ]
    changed = client.put("/api/skills/installed/bundle", json={"skill_md": UPDATED_MD})
    assert changed.status_code == 200, changed.text
    with factory() as db:
        stored = db.query(UserSkill).filter(UserSkill.name == "bundle").one()
        assert {f.path: bytes(f.content) for f in stored.files} == {
            "SKILL.md": UPDATED_MD.encode(),
            "notes.md": b"Companion material",
        }
    replay = client.post(
        "/api/skills/upload", files={"file": ("bundle.zip", archive, "application/zip")}
    )
    assert (
        replay.status_code == 409
    )  # changed bytes cannot be mistaken for an identical retry


def test_bare_markdown_upload_uses_explicit_name(local_api):
    client, _, _ = local_api
    uploaded = client.post(
        "/api/skills/upload",
        data={"name": "my_skill_"},
        files={"file": ("SKILL.md", SKILL_MD.encode(), "text/markdown")},
    )
    assert uploaded.status_code == 200, uploaded.text
    assert uploaded.json()["name"] == "my_skill_"
    assert client.get("/api/skills/installed/my_skill_").json()["content"] == SKILL_MD


@pytest.mark.parametrize(
    "files",
    [
        {"bundle/SKILL.md": SKILL_MD.encode(), "../escape.md": b"bad"},
        {"bundle/SKILL.md": SKILL_MD.encode(), "bundle/template.md": b"\xff\xfe"},
        {"bundle/README.md": b"no skill here"},
    ],
)
def test_invalid_upload_never_publishes_a_row(local_api, files):
    client, _, factory = local_api
    uploaded = client.post(
        "/api/skills/upload",
        files={"file": ("bundle.zip", _zip(files), "application/zip")},
    )
    assert uploaded.status_code == 400, uploaded.text
    with factory() as db:
        assert db.query(UserSkill).count() == 0


def test_invalid_edit_preserves_existing_bundle(local_api):
    client, _, factory = local_api
    assert (
        client.post(
            "/api/skills/create", json={"name": "alpha", "skill_md": SKILL_MD}
        ).status_code
        == 200
    )
    response = client.put("/api/skills/installed/alpha", json={"skill_md": " \u200b\t"})
    assert response.status_code == 400
    with factory() as db:
        skill = db.query(UserSkill).one()
        assert bytes(skill.files[0].content) == SKILL_MD.encode()


def test_team_write_hook_receives_scoped_identity_and_serves_roundtrip(local_api):
    client, actor, _ = local_api

    class TeamProvider:
        def __init__(self):
            self.records = {}
            self.identities = []

        async def list_records(self, context):
            return [
                SkillRecord(name=name, source="team", scope="team", files=files)
                for name, files in self.records.items()
            ]

        async def read_file(self, context, record, path):
            return record.files[path]

        async def create_skill(self, context, *, scope, name, files, **kwargs):
            self.identities.append((context.user_id, scope))
            self.records[name] = files

        async def update_skill_file(self, context, *, scope, name, path, content):
            self.identities.append((context.user_id, scope))
            self.records[name][path] = content

        async def delete_skill(self, context, *, scope, name):
            self.identities.append((context.user_id, scope))
            del self.records[name]

    provider = TeamProvider()
    set_skill_library_provider(provider)
    set_skill_write_provider(provider)
    try:
        created = client.post(
            "/api/skills/create",
            json={"name": "team-a", "skill_md": SKILL_MD, "scope": "team"},
        )
        assert created.status_code == 200, created.text
        assert created.json()["scope"] == "team"
        imported = client.post(
            "/api/skills/upload",
            data={"scope": "team", "name": "team-b"},
            files={"file": ("SKILL.md", SKILL_MD.encode(), "text/markdown")},
        )
        assert imported.status_code == 200, imported.text
        assert imported.json()["scope"] == "team"
        edited = client.put(
            "/api/skills/installed/team-a", json={"skill_md": UPDATED_MD}
        )
        assert edited.status_code == 200, edited.text
        assert (
            client.get("/api/skills/installed/team-a").json()["content"] == UPDATED_MD
        )
        assert client.delete("/api/skills/installed/team-a").status_code == 204
        assert client.delete("/api/skills/installed/team-b").status_code == 204
        assert provider.records == {}
        assert provider.identities == [(actor.id, "team")] * 5
    finally:
        set_skill_library_provider(None)
        set_skill_write_provider(None)


@pytest.mark.parametrize("source", ["builtin", "external", "team"])
def test_nonpersonal_records_cannot_be_modified_without_authorization(
    local_api, source
):
    from xagent.skills.library import (
        SkillWriteProviderError,
        SkillWriteProviderErrorReason,
    )

    client, _, _ = local_api

    class ProtectedProvider:
        async def list_records(self, context):
            return [
                SkillRecord(
                    name="protected",
                    source=source,
                    scope=source,
                    files={"SKILL.md": SKILL_MD.encode()},
                )
            ]

        async def read_file(self, context, record, path):
            return record.files[path]

        async def update_skill_file(self, context, **kwargs):
            raise SkillWriteProviderError(
                SkillWriteProviderErrorReason.FORBIDDEN, "Team skill is read-only."
            )

        async def delete_skill(self, context, **kwargs):
            raise SkillWriteProviderError(
                SkillWriteProviderErrorReason.FORBIDDEN, "Team skill is read-only."
            )

    provider = ProtectedProvider()
    set_skill_library_provider(provider)
    set_skill_write_provider(provider)
    try:
        edited = client.put(
            "/api/skills/installed/protected", json={"skill_md": UPDATED_MD}
        )
        deleted = client.delete("/api/skills/installed/protected")
        assert edited.status_code == deleted.status_code == 403
        assert (
            client.get("/api/skills/installed/protected").json()["content"] == SKILL_MD
        )
        if source == "team":
            assert edited.json()["detail"] == "Team skill is read-only."
            assert deleted.json()["detail"] == "Team skill is read-only."
    finally:
        set_skill_library_provider(None)
        set_skill_write_provider(None)
