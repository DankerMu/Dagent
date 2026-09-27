"""Admin authority must precede any user deletion, including legacy rows."""

import pytest
from fastapi import HTTPException
from sqlalchemy import text

from xagent.web.api.admin_users import delete_user, get_users
from xagent.web.models.auto_model import AutoModelCandidate, AutoModelConfig
from xagent.web.models.database import get_engine
from xagent.web.models.model import Model
from xagent.web.models.user import User, UserModel

from .conftest import _admin_headers, _direct_db_session, _register_second_user, client

pytestmark = pytest.mark.usefixtures("_test_db")


@pytest.mark.asyncio
async def test_local_account_cannot_list_or_delete_another_account():
    _admin_headers()
    _register_second_user("member", "memberpass1")
    db = _direct_db_session()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        member = db.query(User).filter(User.username == "member").one()
        with pytest.raises(HTTPException) as listing:
            await get_users(1, 20, "", member, db)
        assert listing.value.status_code == 403
        with pytest.raises(HTTPException) as deletion:
            await delete_user(int(admin.id), member, db)
        assert deletion.value.status_code == 403
        assert db.query(User).filter(User.id == admin.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_admin_cannot_delete_self_or_nonexistent_account():
    _admin_headers()
    db = _direct_db_session()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        with pytest.raises(HTTPException) as self_delete:
            await delete_user(int(admin.id), admin, db)
        assert self_delete.value.status_code == 400
        with pytest.raises(HTTPException) as missing:
            await delete_user(987654321, admin, db)
        assert missing.value.status_code == 404
        assert db.query(User).filter(User.id == admin.id).count() == 1
    finally:
        db.close()


@pytest.mark.asyncio
async def test_admin_deletion_removes_historical_text2sql_rows():
    _admin_headers()
    _register_second_user("former", "formerpass1")
    db = _direct_db_session()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        former_id = int(db.query(User).filter(User.username == "former").one().id)
        with get_engine().begin() as connection:
            connection.execute(
                text("CREATE TABLE text2sql_databases (user_id INTEGER NOT NULL)")
            )
            connection.execute(
                text("INSERT INTO text2sql_databases (user_id) VALUES (:uid)"),
                {"uid": former_id},
            )
        result = await delete_user(former_id, admin, db)
        assert result["workspace_cleanup_pending"] is False
        db.expire_all()
        assert db.query(User).filter(User.id == former_id).count() == 0
        with get_engine().connect() as connection:
            assert (
                connection.execute(
                    text("SELECT count(*) FROM text2sql_databases")
                ).scalar_one()
                == 0
            )
    finally:
        db.close()


def test_first_admin_requires_a_password_before_opening_local_registration():
    denied = client.post(
        "/api/auth/setup-admin",
        json={
            "username": "admin",
            "email": "admin@example.com",
            "password": "short",  # pragma: allowlist secret - rejected weak fixture password
        },
    )
    assert denied.status_code == 200
    assert denied.json()["success"] is False
    assert client.get("/api/auth/setup-status").json()["needs_setup"] is True


def test_only_admin_controls_local_registration_switch():
    admin_headers = _admin_headers()
    member_headers = _register_second_user("member-switch", "memberpass1")
    forbidden = client.get("/api/auth/register-switch", headers=member_headers)
    assert forbidden.status_code == 403

    changed = client.patch(
        "/api/auth/register-switch",
        json={"enabled": False},
        headers=admin_headers,
    )
    assert changed.status_code == 200
    assert (
        client.get("/api/auth/register-switch", headers=admin_headers).json()[
            "registration_enabled"
        ]
        is False
    )
    assert (
        client.get("/api/auth/register-switch", headers=member_headers).status_code
        == 403
    )


@pytest.mark.asyncio
async def test_admin_deletion_prunes_invisible_historical_model_candidates():
    _admin_headers()
    _register_second_user("former-owner", "formerpass1")
    _register_second_user("surviving-owner", "survivorpass1")
    db = _direct_db_session()
    try:
        admin = db.query(User).filter(User.username == "admin").one()
        former = db.query(User).filter(User.username == "former-owner").one()
        survivor = db.query(User).filter(User.username == "surviving-owner").one()
        former_id, survivor_id = int(former.id), int(survivor.id)
        target = Model(
            model_id="former-target",
            category="llm",
            model_provider="openai-compatible",
            model_name="former-target",
            api_key="",
            base_url="http://127.0.0.1:8001/v1",
            is_active=True,
        )
        router = Model(
            model_id="saved-router",
            category="llm",
            model_provider="openai-compatible",
            model_name="saved-router",
            api_key="",
            base_url="http://127.0.0.1:8001/v1",
            is_active=True,
        )
        db.add_all([target, router])
        db.flush()
        db.add(UserModel(user_id=former_id, model_id=target.id, is_owner=True))
        config = AutoModelConfig(
            user_id=survivor_id, router_model_id=router.id, fallback_model_id=target.id
        )
        db.add(config)
        db.flush()
        db.add(
            AutoModelCandidate(
                config_id=config.id,
                target_model_id=target.id,
                routing_model_id="former-target",
            )
        )
        db.commit()
        config_id = int(config.id)

        await delete_user(former_id, admin, db)

        db.expire_all()
        surviving = (
            db.query(AutoModelConfig).filter(AutoModelConfig.id == config_id).one()
        )
        assert surviving.user_id == survivor_id
        assert surviving.fallback_model_id is None
        assert (
            db.query(AutoModelCandidate)
            .filter(AutoModelCandidate.config_id == config_id)
            .count()
            == 0
        )
    finally:
        db.close()
