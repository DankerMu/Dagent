"""Actor MCP connection identity is immutable across ORM updates."""

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from xagent.web.models.actor_mcp_connection import ActorMCPServerConnection
from xagent.web.models.public_mcp import PublicMCPApp
from xagent.web.models.user import User


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'actor-mcp-owner.db'}")
    User.__table__.create(engine)
    PublicMCPApp.__table__.create(engine)
    ActorMCPServerConnection.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_actor_connection_rejects_owner_rebinding_but_allows_secret_rotation(db):
    user = User(username="owner", password_hash="x")
    catalog = PublicMCPApp(app_id="local-notes", name="Local Notes")
    db.add_all([user, catalog])
    db.flush()
    connection = ActorMCPServerConnection(
        user_id=user.id,
        resource_owner_key="actor:original",
        app_id=catalog.app_id,
        catalog_app_generation=catalog.generation,
        encrypted_env={"TOKEN": "encrypted-initial"},
    )
    db.add(connection)
    db.commit()

    connection.encrypted_env = {"TOKEN": "encrypted-updated"}
    db.commit()
    assert db.get(ActorMCPServerConnection, connection.id).encrypted_env == {
        "TOKEN": "encrypted-updated"
    }
    public_description = repr(connection)
    assert "encrypted-updated" not in public_description
    assert "actor:original" not in public_description
    assert "local-notes" in public_description

    connection.resource_owner_key = "actor:impostor"
    with pytest.raises(ValueError, match="identity and catalog binding are immutable"):
        db.flush()
    db.rollback()
    assert db.get(ActorMCPServerConnection, connection.id).resource_owner_key == (
        "actor:original"
    )


def test_actor_connection_rejects_catalog_generation_rebinding(db):
    user = User(username="owner", password_hash="x")
    catalog = PublicMCPApp(app_id="local-notes", name="Local Notes")
    db.add_all([user, catalog])
    db.flush()
    connection = ActorMCPServerConnection(
        user_id=user.id,
        resource_owner_key="actor:original",
        app_id=catalog.app_id,
        catalog_app_generation=catalog.generation,
    )
    db.add(connection)
    db.commit()

    connection.catalog_app_generation = uuid4()
    with pytest.raises(ValueError, match="identity and catalog binding are immutable"):
        db.flush()
    db.rollback()
    assert db.get(ActorMCPServerConnection, connection.id).catalog_app_generation == (
        catalog.generation
    )
