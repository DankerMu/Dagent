"""Tests for the Chrome MCP connector seed migration."""

import importlib.util
from pathlib import Path
from unittest.mock import patch

import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, text


def _load_migration_module():
    migration_file = (
        Path(__file__).parent.parent.parent
        / "src/xagent/migrations/versions/20260806_seed_chrome_mcp_app.py"
    )
    spec = importlib.util.spec_from_file_location(
        "seed_chrome_migration", migration_file
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _operations(connection):
    return Operations(MigrationContext.configure(connection))


def _create_table(connection):
    connection.execute(
        text(
            """
            CREATE TABLE public_mcp_apps (
                id INTEGER PRIMARY KEY,
                app_id VARCHAR(100) NOT NULL UNIQUE,
                name VARCHAR(200) NOT NULL,
                description TEXT,
                icon VARCHAR(1000),
                transport VARCHAR(50) NOT NULL DEFAULT 'oauth',
                provider_name VARCHAR(50),
                category VARCHAR(100),
                oauth_scopes JSON,
                is_visible_in_connector BOOLEAN NOT NULL DEFAULT 1,
                launch_config JSON
            )
            """
        )
    )


def _app_ids(connection):
    return set(connection.execute(text("SELECT app_id FROM public_mcp_apps")).scalars())


def test_upgrade_inserts_chrome(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
        assert "chrome-devtools" in _app_ids(connection)
        row = connection.execute(
            text(
                "SELECT transport, launch_config, is_visible_in_connector "
                "FROM public_mcp_apps WHERE app_id='chrome-devtools'"
            )
        ).first()
        assert row[0] == "stdio"
        assert "chrome-devtools-mcp" in str(row[1])
        # Assert the persisted column value, not just dict agreement
        # (test_seed_row_matches_registry): the table DDL defaults this
        # column to 1, so a dropped/mistyped key would ship the connector
        # visible — and it must stay hidden until persistent stdio MCP
        # sessions land.
        assert row[2] == 0


def test_upgrade_is_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.upgrade()  # second run must not raise or duplicate
        rows = connection.execute(
            text("SELECT COUNT(*) FROM public_mcp_apps WHERE app_id='chrome-devtools'")
        ).scalar()
        assert rows == 1


def test_upgrade_forces_hidden_on_a_preexisting_colliding_row(tmp_path):
    """Round-8 N1: a hand-created row with this app_id (visible by the table
    default) would otherwise survive the collision branch untouched — and the
    builtin registry overlays the real launch config onto any row sharing the
    app_id at read time, silently yielding a visible, working Chrome
    connector and defeating the hidden-rollout gate. The collision branch
    must enforce hidden instead of returning early, without inserting a
    duplicate or touching the operator's other fields."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        connection.execute(
            text(
                "INSERT INTO public_mcp_apps "
                "(app_id, name, description, transport, is_visible_in_connector) "
                "VALUES ('chrome-devtools', 'Operator Chrome', 'hand-made', "
                "'stdio', 1)"
            )
        )
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
        row = connection.execute(
            text(
                "SELECT COUNT(*), MIN(is_visible_in_connector), MIN(name) "
                "FROM public_mcp_apps WHERE app_id='chrome-devtools'"
            )
        ).first()
        assert row[0] == 1  # no duplicate inserted
        assert row[1] == 0  # forced hidden
        assert row[2] == "Operator Chrome"  # other fields left alone


def test_downgrade_removes_chrome(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()
        assert "chrome-devtools" not in _app_ids(connection)


def test_downgrade_preserves_an_adopted_preexisting_row(tmp_path):
    """Round-9: the collision branch in upgrade() adopts a hand-created row
    (e.g. an operator who created one before this migration deployed, per
    #1143) by flipping only is_visible_in_connector -- it does not overwrite
    name/description/transport. An unconditional DELETE-by-app_id on
    downgrade would then destroy the operator's own row, not "remove the
    entry this migration owns." Pin that upgrade (adopt) -> downgrade
    (restore, not destroy) sequence."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        connection.execute(
            text(
                "INSERT INTO public_mcp_apps "
                "(app_id, name, description, transport, is_visible_in_connector) "
                "VALUES ('chrome-devtools', 'Operator Chrome', 'hand-made', "
                "'stdio', 1)"
            )
        )
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()
        row = connection.execute(
            text(
                "SELECT name, is_visible_in_connector FROM public_mcp_apps "
                "WHERE app_id='chrome-devtools'"
            )
        ).first()
        # Adopted and forced hidden by upgrade(), then left in place by
        # downgrade() -- destroyed would mean row is None.
        assert row is not None
        assert row[0] == "Operator Chrome"
        assert row[1] == 0


def test_downgrade_is_a_noop_when_a_guard_column_is_missing(tmp_path):
    """The downgrade() guard matches on name/description/transport to avoid
    destroying an adopted operator row (test_downgrade_preserves_an_adopted_
    preexisting_row) -- but a reduced-schema table missing one of those
    columns (e.g. mid-migration-chain, same precondition upgrade() already
    tolerates via its column-filter) must not make the DELETE reference a
    nonexistent column and raise.

    It must also not fall back to deleting on whichever guard columns remain:
    fewer guards is a weaker match, which reopens the exact coincidental-match
    risk the three-column guard exists to rule out (see
    test_downgrade_preserves_an_adopted_row_on_a_reduced_schema). No-op and
    leave a hidden orphan row behind instead, same tradeoff as the
    admin-edited-description case."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE public_mcp_apps (
                    id INTEGER PRIMARY KEY,
                    app_id VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    icon VARCHAR(1000),
                    transport VARCHAR(50) NOT NULL DEFAULT 'oauth',
                    provider_name VARCHAR(50),
                    category VARCHAR(100),
                    oauth_scopes JSON,
                    is_visible_in_connector BOOLEAN NOT NULL DEFAULT 1,
                    launch_config JSON
                )
                """
            )
        )
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()  # must not raise: no `description` column
        # No-op, not a delete: the migration's own row is left behind as a
        # hidden orphan rather than risk a weaker, coincidence-prone match.
        assert "chrome-devtools" in _app_ids(connection)


def test_downgrade_preserves_an_adopted_row_on_a_reduced_schema(tmp_path):
    """Round-1 gemini-code-assist major: on a reduced schema missing a guard
    column, the prior fix dropped that column's predicate and deleted on
    whatever guards remained -- e.g. with `description` absent, matching on
    just name+transport. A hand-made operator row is plausibly named 'Chrome'
    with transport 'stdio' (the natural values for a local Chrome connector),
    so that weaker match could delete an adopted operator row and its custom
    fields (icon/category/launch_config), not just the migration's own row.
    Pin that such a row -- and its customizations -- survive both upgrade()'s
    adoption and downgrade()'s no-op."""
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE public_mcp_apps (
                    id INTEGER PRIMARY KEY,
                    app_id VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    icon VARCHAR(1000),
                    transport VARCHAR(50) NOT NULL DEFAULT 'oauth',
                    provider_name VARCHAR(50),
                    category VARCHAR(100),
                    oauth_scopes JSON,
                    is_visible_in_connector BOOLEAN NOT NULL DEFAULT 1,
                    launch_config JSON
                )
                """
            )
        )
        connection.execute(
            text(
                "INSERT INTO public_mcp_apps "
                "(app_id, name, icon, transport, category, is_visible_in_connector) "
                "VALUES ('chrome-devtools', 'Chrome', 'https://operator.example/icon.png', "
                "'stdio', 'Operator Category', 1)"
            )
        )
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()
        row = connection.execute(
            text(
                "SELECT icon, category, is_visible_in_connector "
                "FROM public_mcp_apps WHERE app_id='chrome-devtools'"
            )
        ).first()
        assert row is not None
        assert row[0] == "https://operator.example/icon.png"
        assert row[1] == "Operator Category"
        assert row[2] == 0  # adopted and forced hidden by upgrade()


def test_downgrade_then_upgrade_round_trip(tmp_path):
    """Round-6 MINOR-4: downgrade().upgrade() had no coverage. A downgrade
    only deletes the catalog row (leftover MCPServer/UserMCPServer rows are
    intentionally left in place per the downgrade docstring), so a
    subsequent upgrade must cleanly re-seed it rather than hitting the
    existing-row early return or a uniqueness conflict.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        _create_table(connection)
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()
            migration.upgrade()
        rows = connection.execute(
            text("SELECT COUNT(*) FROM public_mcp_apps WHERE app_id='chrome-devtools'")
        ).scalar()
        assert rows == 1
        assert "chrome-devtools" in _app_ids(connection)


def test_upgrade_and_downgrade_are_no_ops_without_the_table(tmp_path):
    """Round-6 MINOR-4: the early-return branch (both directions) when
    public_mcp_apps doesn't exist yet -- e.g. a fresh database mid-migration
    chain, before the migration that creates the table has run. Must not
    raise; must not create the table itself.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        with patch.object(migration, "op", _operations(connection)):
            migration.upgrade()
            migration.downgrade()
        existing_tables = set(sa.inspect(connection).get_table_names())
        assert "public_mcp_apps" not in existing_tables


def test_upgrade_raises_if_visibility_column_is_missing(tmp_path):
    """Round-6 MINOR-4/nit: exercises the RuntimeError path directly (the
    migration test suite runs without -O, so the assert-vs-raise distinction
    is otherwise never actually executed by this suite). A table missing
    is_visible_in_connector must fail loudly rather than seed the chrome row
    visible via the column-filter's silent drop.
    """
    engine = create_engine(f"sqlite:///{tmp_path / 'test.db'}")
    migration = _load_migration_module()
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                CREATE TABLE public_mcp_apps (
                    id INTEGER PRIMARY KEY,
                    app_id VARCHAR(100) NOT NULL UNIQUE,
                    name VARCHAR(200) NOT NULL,
                    description TEXT,
                    icon VARCHAR(1000),
                    transport VARCHAR(50) NOT NULL DEFAULT 'oauth',
                    provider_name VARCHAR(50),
                    category VARCHAR(100),
                    oauth_scopes JSON,
                    launch_config JSON
                )
                """
            )
        )
        with patch.object(migration, "op", _operations(connection)):
            try:
                migration.upgrade()
                raised = False
            except RuntimeError as exc:
                raised = True
                assert "is_visible_in_connector" in str(exc)
        assert raised, "upgrade() must raise when the visibility column is missing"
        assert "chrome-devtools" not in _app_ids(connection)
