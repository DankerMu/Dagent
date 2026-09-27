"""Persisted user SQL connections and environment fallback at the service boundary."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from xagent.web.models.tool_config import UserToolConfig
from xagent.web.models.user import User
from xagent.web.services.tool_credentials import (
    delete_sql_connection,
    get_sql_connection_map,
    list_sql_connections,
    resolve_sql_connection,
    set_sql_connection,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sql-connections.db'}")
    User.__table__.create(engine)
    UserToolConfig.__table__.create(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def test_user_connection_precedes_environment_without_exposing_password(
    db, monkeypatch
):
    env_url = "postgresql://env:environment-secret@localhost/warehouse"  # pragma: allowlist secret - synthetic DSN, never connected
    owner_url = "postgresql://owner:owner-secret@localhost/warehouse"  # pragma: allowlist secret - synthetic DSN, never connected
    monkeypatch.setenv("XAGENT_EXTERNAL_DB_WAREHOUSE", env_url)

    set_sql_connection(db, 1, " warehouse ", owner_url)
    assert resolve_sql_connection(db, 1, "Warehouse") == owner_url
    assert get_sql_connection_map(db, 1)["WAREHOUSE"] == owner_url
    assert resolve_sql_connection(db, 2, "warehouse") == env_url

    owner = list_sql_connections(db, 1)
    other = list_sql_connections(db, 2)
    assert owner == [
        {
            "name": "WAREHOUSE",
            "source": "db",
            "masked": "postgresql://owner:***@localhost/warehouse",
            "configured": True,
        }
    ]
    assert other == [
        {
            "name": "WAREHOUSE",
            "source": "env",
            "masked": "postgresql://env:***@localhost/warehouse",
            "configured": True,
        }
    ]
    assert "owner-secret" not in str(db.query(UserToolConfig).one().config)

    delete_sql_connection(db, 1, " WAREHOUSE ")
    assert resolve_sql_connection(db, 1, "warehouse") == env_url
    assert list_sql_connections(db, 1) == other


def test_corrupt_stored_connection_falls_back_to_environment_without_leaking(
    db, monkeypatch
):
    env_url = "sqlite:///fallback.db"
    monkeypatch.setenv("XAGENT_EXTERNAL_DB_ARCHIVE", env_url)
    db.add(
        UserToolConfig(
            user_id=1,
            tool_name="sql_query",
            config={"sql_connections": {"ARCHIVE": {"ciphertext": "invalid-token"}}},
        )
    )
    db.commit()

    assert resolve_sql_connection(db, 1, "archive") == env_url
    assert get_sql_connection_map(db, 1)["ARCHIVE"] == env_url
    assert "invalid-token" not in str(list_sql_connections(db, 1))


@pytest.mark.parametrize(
    "connection_url", ["not a url", "https://localhost/service", " "]
)
def test_invalid_connection_is_rejected_without_displacing_existing_value(
    db, monkeypatch, connection_url
):
    monkeypatch.setenv("XAGENT_EXTERNAL_DB_REPORTS", "sqlite:///reports.db")
    with pytest.raises(ValueError):
        set_sql_connection(db, 1, "reports", connection_url)
    assert resolve_sql_connection(db, 1, "reports") == "sqlite:///reports.db"
    assert db.query(UserToolConfig).count() == 0
