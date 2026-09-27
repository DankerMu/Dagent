"""``check_task_status_enum_drift`` (``models/task.py``).

Companion to test_task_status_storage_postgresql.py's
``test_pg_enum_reflects_exactly_the_taskstatus_members``, which pins that a
*fresh* ``create_all`` schema has no drift -- it cannot observe a deployed
database whose enum type predates a later addition to ``TaskStatus``. This
file is that missing half: it builds the ``taskstatus`` type by hand, with a
label set the test controls directly, so it can put the check in front of a
type that has actually drifted rather than one ``create_all`` always gets
right.

Disposable-database plumbing is the one this repository already centralizes
for this exact need (``tests/shared/postgres_disposable.py``, extracted from
two near-identical fixtures for the same reason this file would otherwise
duplicate a third).
"""

from __future__ import annotations

import pytest
import sqlalchemy as sa
from sqlalchemy import text

from tests.shared.postgres_disposable import disposable_database_factory
from xagent.web.models.database import Base, _initialize_database_schema
from xagent.web.models.task import (
    TASKSTATUS_ENUM_REPAIR_REVISION,
    Task,
    TaskStatus,
    TaskStatusEnumDriftError,
    check_task_status_enum_drift,
)

# Same source as check_task_status_enum_drift's own expected_labels: the
# status column's declared enum labels, not TaskStatus's member names. The
# two happen to agree today (see test_expected_labels_match_taskstatus_members
# below for why, and what it means when they stop), but building the
# fixture labels from the member names directly would let this file drift
# out of sync with what the guard actually compares against.
_ALL_LABELS = list(Task.__table__.c.status.type.enums)


def _label_list_sql(labels: list[str]) -> str:
    return ", ".join(f"'{label}'" for label in labels)


def _create_taskstatus_type(
    conn: sa.Connection, labels: list[str], schema: str | None = None
) -> None:
    """A ``taskstatus`` enum carrying exactly ``labels``, in ``schema`` when
    one is named and in whatever ``search_path`` picks otherwise."""
    qualified = f"{schema}.taskstatus" if schema else "taskstatus"
    conn.execute(text(f"CREATE TYPE {qualified} AS ENUM ({_label_list_sql(labels)})"))


def _create_taskstatus_type_and_table(
    conn: sa.Connection, labels: list[str], schema: str | None = None
) -> None:
    """Minimal ``taskstatus`` enum plus a ``tasks`` table whose ``status``
    column is declared with it -- enough for ``check_task_status_enum_drift``
    to see (it resolves ``tasks``, reads the type of that table's ``status``
    column, and lists that type's labels), without going through the full
    ``Task`` ORM schema this check has no other dependency on."""
    _create_taskstatus_type(conn, labels, schema)
    prefix = f"{schema}." if schema else ""
    type_name = f"{schema}.taskstatus" if schema else "taskstatus"
    conn.execute(
        text(
            f"CREATE TABLE {prefix}tasks (id SERIAL PRIMARY KEY, "
            f"status {type_name} NOT NULL DEFAULT '{labels[0]}')"
        )
    )


@pytest.fixture()
def postgresql_engine_factory():
    with disposable_database_factory("xagent_taskstatus_drift") as make:
        yield make


def test_expected_labels_match_taskstatus_members() -> None:
    """Sentinel, not a functional test of the guard itself.

    check_task_status_enum_drift's expected_labels comes from
    Task.__table__.c.status.type.enums (the status column's own declared
    labels), not from iterating TaskStatus's member names. The two sets are
    equal only because the column has no values_callable, so SQLAlchemy's
    default -- persist the member name -- is in effect. Nothing keeps that
    true: give Task.status a values_callable that persists something else
    (member values, for instance) and this equality breaks.

    test_pg_enum_reflects_exactly_the_taskstatus_members
    (test_task_status_storage_postgresql.py) pins the same equality, but
    its db_session fixture skips whenever XAGENT_TEST_POSTGRES_URL is
    unset, and the ordinary test matrix never sets it -- so that cell is
    green-by-skip in exactly the runs where a values_callable change would
    first appear. It also compares a different pair of sources (a real
    create_all schema vs. TaskStatus's member names), so its failure
    message blames create_all rather than the column declaration. This
    test opens no database, so it actually executes in the ordinary
    matrix. A red run here means the column's storage format is changing
    and needs a data migration for every existing row, not just a code
    change.
    """
    assert {member.name for member in TaskStatus} == set(
        Task.__table__.c.status.type.enums
    )


@pytest.mark.postgresql
def test_pg_enum_labels_match_passes(postgresql_engine_factory) -> None:
    """A correct schema must pass. Both label sets here come from the same
    ``TaskStatus`` class (the live one via ``create_all``, expected from
    the status column's declared labels), so this cell cannot fail on a
    genuine label mismatch -- that direction is covered by the failure
    cells below. What it does pin is the opposite direction: the check
    must not reject a deployment whose enum is correct, which is the
    regression a stricter query (an unqualified type name, a missing
    visibility predicate) would introduce.
    """
    engine = postgresql_engine_factory("correct")
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        check_task_status_enum_drift(conn)


@pytest.mark.postgresql
def test_pg_enum_missing_label_raises(postgresql_engine_factory) -> None:
    engine = postgresql_engine_factory("missing")
    missing_label = _ALL_LABELS[-1]
    present_labels = _ALL_LABELS[:-1]
    with engine.begin() as conn:
        _create_taskstatus_type_and_table(conn, present_labels)

    with engine.connect() as conn:
        with pytest.raises(TaskStatusEnumDriftError) as exc_info:
            check_task_status_enum_drift(conn)

    message = str(exc_info.value)
    assert missing_label in message
    assert "Upgrade this database to the Alembic head" in message
    assert TASKSTATUS_ENUM_REPAIR_REVISION in message
    # Exclusive, not merely present: the combined branch carries the same
    # upgrade instruction, so only the absence of its own wording
    # distinguishes them.
    assert "unexpected label(s) indicate" not in message
    assert "DROP VALUE" not in message


@pytest.mark.postgresql
def test_pg_enum_extra_label_raises(postgresql_engine_factory) -> None:
    engine = postgresql_engine_factory("extra")
    extra_label = "ARCHIVED_LEGACY"
    with engine.begin() as conn:
        _create_taskstatus_type_and_table(conn, [*_ALL_LABELS, extra_label])

    with engine.connect() as conn:
        with pytest.raises(TaskStatusEnumDriftError) as exc_info:
            check_task_status_enum_drift(conn)

    message = str(exc_info.value)
    assert extra_label in message
    assert "No migration can remove them" in message
    assert "ALTER TYPE" in message
    assert "DROP VALUE" in message
    # The combined branch carries ALTER TYPE and DROP VALUE too, so those
    # alone cannot tell the two apart; these two exclusions can. No migration
    # repairs an unexpected label, so the upgrade instruction must not appear
    # on this branch.
    assert "Upgrade this database to the Alembic head" not in message
    assert "unexpected label(s) indicate" not in message


@pytest.mark.postgresql
def test_pg_enum_missing_and_extra_labels_name_both_in_the_message(
    postgresql_engine_factory,
) -> None:
    """Both directions can be true at once -- a process older than the
    database (unexpected labels present) that is also missing a label a
    newer process added (a concurrent-deployment window, not just a stale
    one). The remediation sentence has to say both things are true rather
    than picking one, since only one of them has a migration that fixes it.
    """
    engine = postgresql_engine_factory("both")
    extra_label = "ARCHIVED_LEGACY"
    with engine.begin() as conn:
        _create_taskstatus_type_and_table(conn, [*_ALL_LABELS[:-1], extra_label])

    with engine.connect() as conn:
        with pytest.raises(TaskStatusEnumDriftError) as exc_info:
            check_task_status_enum_drift(conn)

    message = str(exc_info.value)
    assert _ALL_LABELS[-1] in message
    assert extra_label in message
    assert "Upgrade this database to the Alembic head" in message
    assert "cannot be reconciled by a migration" in message


@pytest.mark.postgresql
def test_missing_tasks_table_is_a_noop(postgresql_engine_factory) -> None:
    """A bind whose schema has not been created yet -- ``has_table("tasks")``
    is false, so the check has nothing to compare and must not raise. The
    startup path itself never reaches this: it runs after schema creation.
    This covers a caller that doesn't.
    """
    engine = postgresql_engine_factory("empty")
    with engine.connect() as conn:
        check_task_status_enum_drift(conn)  # must not raise


def test_sqlite_backend_is_a_noop() -> None:
    """Any non-PostgreSQL backend is a no-op before the check ever touches
    ``pg_catalog`` -- which doesn't exist on SQLite, so if the dialect guard
    didn't fire, this would raise ``OperationalError`` rather than pass
    silently.

    Builds the real schema first (``tasks`` present) rather than using an
    empty database: an empty database returns early through the
    schema-not-created-yet guard regardless of dialect, which would let a
    missing or deleted dialect guard pass this test by accident. With
    ``tasks`` present, the dialect guard is the only thing standing between
    this call and a query ``pg_catalog`` doesn't have.
    """
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        check_task_status_enum_drift(conn)  # must not raise, must not query


def test_sqlite_backend_never_reaches_the_catalog_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same claim as above, proven the other way: replace ``text`` in
    ``models/task.py`` with a spy that raises if called, so a failure to
    short-circuit on ``bind.dialect.name`` shows up as an assertion failure
    naming the exact reason, not a generic ``OperationalError`` fifteen
    frames down in the sqlite3 driver. Same schema requirement as the cell
    above, for the same reason: an empty database would pass this test
    whether or not the dialect guard exists.
    """
    import xagent.web.models.task as task_module

    def _spy(*_args: object, **_kwargs: object) -> None:
        raise AssertionError(
            "check_task_status_enum_drift queried the catalog on a "
            "non-PostgreSQL bind instead of returning early"
        )

    monkeypatch.setattr(task_module, "text", _spy)
    engine = sa.create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    with engine.connect() as conn:
        check_task_status_enum_drift(conn)


@pytest.mark.postgresql
def test_shadow_type_first_on_search_path_does_not_mask_a_missing_label(
    postgresql_engine_factory,
) -> None:
    """``search_path = shadow, app``: ``shadow`` holds a complete
    ``taskstatus`` and no ``tasks`` at all, while the ``tasks`` that does
    resolve lives in ``app`` and its ``status`` column is missing a label.
    Relation visibility and type visibility are separate resolutions in
    PostgreSQL, so a check that finds the type by name reads ``shadow``'s
    complete copy, sees no drift, and lets the process start against a
    database whose next write of that label will fail.

    The assertion names the missing label exactly rather than merely
    looking for it in the message: with the application schema deliberately
    somewhere other than ``public``, hard-coding a schema onto the
    ``to_regclass`` argument would resolve nothing, report every member as
    missing, and still contain this label.
    """
    engine = postgresql_engine_factory("shadow_masks")
    missing_label = _ALL_LABELS[-1]
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA shadow"))
        conn.execute(text("CREATE SCHEMA app"))
        _create_taskstatus_type(conn, _ALL_LABELS, schema="shadow")
        _create_taskstatus_type_and_table(conn, _ALL_LABELS[:-1], schema="app")

    with engine.connect() as conn:
        conn.execute(text("SET search_path TO shadow, app"))
        with pytest.raises(TaskStatusEnumDriftError) as exc_info:
            check_task_status_enum_drift(conn)

    message = str(exc_info.value)
    assert f"missing labels ['{missing_label}']" in message
    assert "unexpected labels []" in message


@pytest.mark.postgresql
def test_shadow_type_first_on_search_path_does_not_reject_a_correct_column(
    postgresql_engine_factory,
) -> None:
    """The other direction of the same asymmetry: ``shadow`` is first on
    the search path and holds a ``taskstatus`` carrying a label this
    application does not know, but no ``tasks``; the ``tasks`` that
    resolves is in ``app`` and its ``status`` column's type is exactly
    right. Finding the type by name would read ``shadow``'s copy and
    refuse to start a correct deployment.
    """
    engine = postgresql_engine_factory("shadow_rejects")
    with engine.begin() as conn:
        conn.execute(text("CREATE SCHEMA shadow"))
        conn.execute(text("CREATE SCHEMA app"))
        _create_taskstatus_type(conn, [*_ALL_LABELS, "SHADOWED_EXTRA"], schema="shadow")
        _create_taskstatus_type_and_table(conn, _ALL_LABELS, schema="app")

    with engine.connect() as conn:
        conn.execute(text("SET search_path TO shadow, app"))
        check_task_status_enum_drift(conn)  # must not raise


@pytest.mark.postgresql
def test_startup_refuses_to_serve_a_database_with_an_unrepairable_extra_label(
    postgresql_engine_factory,
) -> None:
    """Drive the real ``_initialize_database_schema`` path and pin the refusal
    observed on the one drift shape no migration can heal.

    An extra, unrecognized label is deliberately not the missing-label shape
    the taskstatus-repair migration (``20260901_taskstatus_waiting_for_user``)
    exists for: ``ALTER TYPE ... ADD VALUE`` only ever adds labels, so
    ``try_upgrade_db`` running ahead of this check cannot make an extra label
    disappear the way it makes a missing one appear. On an otherwise complete
    database with an extra label, the startup check must refuse to serve.
    """
    engine = postgresql_engine_factory("unrepairable_extra_label")
    extra_label = "LEGACY_EXTRA"

    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        connection.execute(
            text("CREATE TABLE alembic_version (version_num VARCHAR(255) NOT NULL)")
        )
        connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": TASKSTATUS_ENUM_REPAIR_REVISION},
        )
        connection.execute(text(f"ALTER TYPE taskstatus ADD VALUE '{extra_label}'"))

    with pytest.raises(TaskStatusEnumDriftError) as exc_info:
        _initialize_database_schema(engine)

    message = str(exc_info.value)
    assert extra_label in message
    # The extra-only branch's own wording (see check_task_status_enum_drift):
    # exclusive to this branch is exactly the point, since it is what a
    # nested-under-seed-condition mutation removes.
    assert "No migration can remove them" in message
