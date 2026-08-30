"""The schema, as an ordered list of migrations that only ever grows.

Migrations rather than a single `CREATE TABLE` script because a local database
outlives the code that made it: a developer who ran the pilot last week must be able
to pull and run it again without deleting their data. Each entry is applied once, in
order, inside its own transaction, and recorded in ``schema_migrations``.

Immutability is enforced by the database, not by the adapter. ``cycle_snapshots`` and
``interviews`` carry ABORT triggers on UPDATE and DELETE, so a completed record cannot
be revised by any code path -- including a future one written by somebody who did not
read this module. That is the difference between an invariant and a convention.
"""

from __future__ import annotations

import sqlite3
from typing import NamedTuple

__all__ = ["MIGRATIONS", "Migration", "apply_migrations", "current_version"]


class Migration(NamedTuple):
    """One forward step of the schema."""

    version: int
    name: str
    statements: tuple[str, ...]


_INITIAL = (
    """
    CREATE TABLE runs (
        run_id           TEXT PRIMARY KEY,
        schema_version   INTEGER NOT NULL,
        run_kind         TEXT    NOT NULL,
        status           TEXT    NOT NULL,
        current_cycle    INTEGER NOT NULL,
        version          INTEGER NOT NULL,
        paused           INTEGER NOT NULL DEFAULT 0,
        configuration    TEXT    NOT NULL,
        usage            TEXT    NOT NULL,
        created_at       TEXT    NOT NULL,
        updated_at       TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE cycle_locks (
        run_id         TEXT PRIMARY KEY REFERENCES runs(run_id) ON DELETE CASCADE,
        schema_version INTEGER NOT NULL,
        cycle          INTEGER NOT NULL,
        token          TEXT    NOT NULL,
        invocation_id  TEXT    NOT NULL,
        acquired_at    TEXT    NOT NULL,
        expires_at     TEXT    NOT NULL
    )
    """,
    """
    CREATE TABLE prepared_cycles (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        cycle          INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        content_hash   TEXT    NOT NULL,
        invocation_id  TEXT    NOT NULL,
        committed      INTEGER NOT NULL DEFAULT 0,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        updated_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, cycle)
    )
    """,
    """
    CREATE TABLE cycle_snapshots (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        arm_id         TEXT    NOT NULL,
        cycle          INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        snapshot_hash  TEXT    NOT NULL,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, arm_id, cycle)
    )
    """,
    "CREATE INDEX cycle_snapshots_by_cycle ON cycle_snapshots (run_id, cycle)",
    """
    CREATE TRIGGER cycle_snapshots_are_immutable
    BEFORE UPDATE ON cycle_snapshots
    BEGIN
        SELECT RAISE(ABORT, 'a committed cycle snapshot cannot be modified');
    END
    """,
    """
    CREATE TRIGGER cycle_snapshots_are_permanent
    BEFORE DELETE ON cycle_snapshots
    WHEN (SELECT COUNT(*) FROM runs WHERE run_id = OLD.run_id) > 0
    BEGIN
        SELECT RAISE(ABORT, 'a committed cycle snapshot cannot be deleted');
    END
    """,
    """
    CREATE TABLE arm_current_states (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        arm_id         TEXT    NOT NULL,
        schema_version INTEGER NOT NULL,
        cycle          INTEGER NOT NULL,
        state_hash     TEXT    NOT NULL,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        updated_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, arm_id)
    )
    """,
    """
    CREATE TABLE interviews (
        run_id           TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        arm_id           TEXT    NOT NULL,
        cycle            INTEGER NOT NULL,
        schema_version   INTEGER NOT NULL,
        record_hash      TEXT    NOT NULL,
        input_state_hash TEXT    NOT NULL,
        payload          TEXT    NOT NULL,
        created_at       TEXT    NOT NULL,
        PRIMARY KEY (run_id, arm_id, cycle)
    )
    """,
    """
    CREATE TRIGGER interviews_are_immutable
    BEFORE UPDATE ON interviews
    BEGIN
        SELECT RAISE(ABORT, 'an interview is a measurement and cannot be revised');
    END
    """,
    """
    CREATE TABLE metrics (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        metric_name    TEXT    NOT NULL,
        arm_id         TEXT    NOT NULL,
        cycle          INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        value          REAL    NOT NULL,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        updated_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, metric_name, arm_id, cycle)
    )
    """,
    """
    CREATE TABLE embeddings (
        run_id         TEXT NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        key            TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        payload        TEXT NOT NULL,
        created_at     TEXT NOT NULL,
        PRIMARY KEY (run_id, key)
    )
    """,
    """
    CREATE TABLE token_counts (
        counter_version TEXT    NOT NULL,
        text_hash       TEXT    NOT NULL,
        schema_version  INTEGER NOT NULL,
        tokens          INTEGER NOT NULL,
        created_at      TEXT    NOT NULL,
        PRIMARY KEY (counter_version, text_hash)
    )
    """,
    """
    CREATE TABLE analysis_status (
        run_id           TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        analysis_name    TEXT    NOT NULL,
        schema_version   INTEGER NOT NULL,
        metric_version   TEXT    NOT NULL,
        completed_cycles TEXT    NOT NULL,
        created_at       TEXT    NOT NULL,
        updated_at       TEXT    NOT NULL,
        PRIMARY KEY (run_id, analysis_name)
    )
    """,
    """
    CREATE TABLE export_manifests (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        export_id      TEXT    NOT NULL,
        schema_version INTEGER NOT NULL,
        run_kind       TEXT    NOT NULL,
        directory      TEXT    NOT NULL,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, export_id)
    )
    """,
)


_ANALYSIS_ARTIFACTS = (
    """
    CREATE TABLE analysis_artifacts (
        run_id         TEXT    NOT NULL REFERENCES runs(run_id) ON DELETE CASCADE,
        name           TEXT    NOT NULL,
        schema_version INTEGER NOT NULL,
        payload        TEXT    NOT NULL,
        created_at     TEXT    NOT NULL,
        updated_at     TEXT    NOT NULL,
        PRIMARY KEY (run_id, name)
    )
    """,
)
"""Derived analysis output that is too large to recompute per request.

The Graveyard is derived on demand because it is a projection of snapshots the
reader already has. Echo measurements and contradiction classifications are not:
they cost embeddings and, sometimes, a model call. Storing them is what lets the
read API stay a read API."""


MIGRATIONS: tuple[Migration, ...] = (
    Migration(version=1, name="initial_pilot_schema", statements=_INITIAL),
    Migration(version=2, name="analysis_artifacts", statements=_ANALYSIS_ARTIFACTS),
)
"""Every migration, in the order they are applied. Append only; never edit."""


def current_version(connection: sqlite3.Connection) -> int:
    """The highest migration this database has applied, or 0 for an empty one."""
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        " version INTEGER PRIMARY KEY,"
        " name TEXT NOT NULL,"
        " applied_at TEXT NOT NULL)"
    )
    row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    return int(row[0]) if row and row[0] is not None else 0


def apply_migrations(connection: sqlite3.Connection, *, now: str) -> tuple[int, ...]:
    """Apply every migration this database has not seen, in order.

    Each migration runs inside its own transaction, so a failure part-way through
    leaves the database at the last version that fully applied rather than at a
    version that half applied.

    Returns:
        The versions that were applied by this call.
    """
    applied: list[int] = []
    for migration in MIGRATIONS:
        if migration.version <= current_version(connection):
            continue
        try:
            connection.execute("BEGIN")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, now),
            )
            connection.execute("COMMIT")
        except Exception:
            connection.execute("ROLLBACK")
            raise
        applied.append(migration.version)
    return tuple(applied)
