"""SQL factory input is detached per build and fails closed on checkout errors."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from xagent.core.tools.adapters.vibe.selection_spec import ToolSelectionSpec
from xagent.web.models.tool_config import UserToolConfig
from xagent.web.models.user import User
from xagent.web.services.tool_credentials import set_sql_connection
from xagent.web.tools.config import WebToolConfig


@pytest.fixture
def sql_runtime(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'sql-factory.db'}")
    User.__table__.create(engine)
    UserToolConfig.__table__.create(engine)
    factory = sessionmaker(bind=engine)
    cfg = WebToolConfig(
        db=None,
        request=None,
        db_factory=factory,
        user_id=1,
        include_mcp_tools=False,
        tool_selection_spec=ToolSelectionSpec.from_raw(tool_categories=["database"]),
    )
    yield engine, factory, cfg
    cfg.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_sql_factory_snapshot_is_stable_until_next_prepare(sql_runtime):
    _, factory, cfg = sql_runtime
    with factory() as db:
        set_sql_connection(db, 1, "warehouse", "sqlite:///old.db")

    await cfg.prepare_factory_runtime()
    assert cfg.get_sql_connections()["WAREHOUSE"] == "sqlite:///old.db"
    with factory() as db:
        set_sql_connection(db, 1, "warehouse", "sqlite:///new.db")

    assert cfg.get_sql_connections()["WAREHOUSE"] == "sqlite:///old.db"
    await cfg.prepare_factory_runtime()
    assert cfg.get_sql_connections()["WAREHOUSE"] == "sqlite:///new.db"


@pytest.mark.asyncio
async def test_sql_factory_snapshot_read_failure_never_becomes_empty_credentials(
    sql_runtime,
    monkeypatch,
):
    engine, _, cfg = sql_runtime
    monkeypatch.setenv("XAGENT_EXTERNAL_DB_WAREHOUSE", "sqlite:///fallback.db")

    def fail_sql_read(connection, cursor, statement, parameters, context, executemany):
        if "user_tool_configs" in statement:
            raise RuntimeError("database-lost")

    event.listen(engine, "before_cursor_execute", fail_sql_read)
    try:
        await cfg.prepare_factory_runtime()
        with pytest.raises(
            RuntimeError, match="SQL connection snapshot is unavailable"
        ):
            cfg.get_sql_connections()
    finally:
        event.remove(engine, "before_cursor_execute", fail_sql_read)

    await cfg.prepare_factory_runtime()
    assert cfg.get_sql_connections()["WAREHOUSE"] == "sqlite:///fallback.db"
