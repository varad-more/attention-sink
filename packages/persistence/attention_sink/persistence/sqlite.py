"""The SQLite adapter: the only module in this repository that knows about tables.

Implements :class:`~attention_sink.pilot.repositories.PilotRepository`. Everything
above it holds the protocol and never this class, so Phase 7's DynamoDB adapter is a
second implementation rather than a rewrite of the services.

Two things here are load-bearing rather than incidental.

**The cycle commit is one transaction.** Six snapshot inserts, six state updates, the
prepared-cycle flag, the run advance, the usage counters, and the lock release either
all happen or none of them does. Five arms that advanced and one that did not is no
longer the same experiment, and there is no repair for it after the fact.

**Immutability is a database trigger, not a convention.** ``cycle_snapshots`` and
``interviews`` refuse UPDATE outright. An adapter method that tried to revise one
would fail, which is the point: the guarantee survives code nobody has written yet.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Self

from attention_sink.domain import ArmId, MemoryState, MetricEvidence
from attention_sink.persistence.migrations import apply_migrations, current_version
from attention_sink.pilot import ModelUsage, RunStatus
from attention_sink.pilot.repositories import (
    AnalysisStatus,
    ConcurrentRunUpdate,
    CycleLock,
    ExportManifestRecord,
    LockNotHeld,
    PersistenceError,
    PreparedCycle,
    PreparedCycleConflict,
    RunRecord,
    StoredInterview,
)
from attention_sink.pilot.snapshots import ArmCycleSnapshot

__all__ = ["DEFAULT_DATABASE_PATH", "DEFAULT_LOCK_TTL_SECONDS", "SqliteRepository"]

DEFAULT_DATABASE_PATH = Path(".pilot-local/pilot.sqlite3")
"""Where a local run keeps its database, relative to the repository root."""

DEFAULT_LOCK_TTL_SECONDS = 300
"""How long a cycle lease lasts. Long enough for twenty-four fixture cycles at once,
short enough that a scheduler killed mid-cycle unwedges itself within five minutes."""


def _now() -> datetime:
    return datetime.now(UTC)


def _dumps(value: Any) -> str:
    """Serialise a record for storage, sorted so a row diff is readable."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


class SqliteRepository:
    """A transactional local store for one pilot database.

    Not thread-safe by construction: SQLite connections are not, and the pilot's
    concurrency is inside a cycle rather than across them. The scheduler runs one
    cycle at a time and the API is read-only, so a connection per repository is
    enough and a pool would be a moving part with nothing to do.
    """

    def __init__(self, path: Path | str = DEFAULT_DATABASE_PATH, *, clock: Any = _now) -> None:
        """Open (and create) the database at ``path`` and migrate it forward."""
        self.path = Path(path)
        self.clock = clock
        if self.path.parent != Path():
            self.path.parent.mkdir(parents=True, exist_ok=True)
        # ``check_same_thread=False`` because the read API serves sync endpoints from
        # a threadpool. Safe here for one specific reason: every write goes through
        # ``_transaction``, which takes SQLite's write lock with BEGIN IMMEDIATE, and
        # the API never writes at all. A future adapter that wrote from several
        # threads would need a connection per thread, not this flag.
        self._connection = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self.migrate()

    # ------------------------------------------------------------- lifecycle

    def migrate(self) -> tuple[int, ...]:
        """Apply every outstanding migration. Safe to call on every open."""
        return apply_migrations(self._connection, now=self.clock().isoformat())

    @property
    def schema_version(self) -> int:
        """The migration version this database is at."""
        return current_version(self._connection)

    def close(self) -> None:
        """Close the connection. Idempotent."""
        self._connection.close()

    def __enter__(self) -> Self:
        """Enter a context that closes the connection on the way out."""
        return self

    def __exit__(self, *_: object) -> None:
        """Close the connection."""
        self.close()

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        """Run a block inside one immediate transaction, rolling back on anything.

        ``BEGIN IMMEDIATE`` rather than the default deferred begin: the writes here
        are known in advance, and taking the write lock up front turns a lost race
        into a clean failure instead of a mid-transaction upgrade that can deadlock.
        """
        self._connection.execute("BEGIN IMMEDIATE")
        try:
            yield self._connection
        except Exception:
            self._connection.execute("ROLLBACK")
            raise
        self._connection.execute("COMMIT")

    # ------------------------------------------------------------------ runs

    def create_run(self, record: RunRecord) -> RunRecord:
        """Insert a new run.

        Raises:
            PersistenceError: A run with that identifier already exists.
        """
        try:
            with self._transaction() as connection:
                connection.execute(
                    "INSERT INTO runs (run_id, schema_version, run_kind, status, current_cycle,"
                    " version, paused, configuration, usage, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.run_id,
                        record.schema_version,
                        record.run_kind.value,
                        record.status.value,
                        record.current_cycle,
                        record.version,
                        int(record.paused),
                        _dumps(record.configuration.model_dump(mode="json")),
                        _dumps(record.usage.model_dump(mode="json")),
                        record.created_at.isoformat(),
                        record.updated_at.isoformat(),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            msg = f"run {record.run_id} already exists"
            raise PersistenceError(msg) from exc
        return record

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return one run's head, or None when it does not exist."""
        row = self._connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return None if row is None else _run_from_row(row)

    def list_runs(self) -> tuple[RunRecord, ...]:
        """Every run, newest first."""
        rows = self._connection.execute("SELECT * FROM runs ORDER BY created_at DESC, run_id")
        return tuple(_run_from_row(row) for row in rows)

    def update_run_status(self, run_id: str, *, status: RunStatus, version: int) -> RunRecord:
        """Move a run to ``status``, if it is still at ``version``.

        Raises:
            ConcurrentRunUpdate: The run has moved on, or does not exist.
        """
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ?, version = version + 1, updated_at = ?"
                " WHERE run_id = ? AND version = ?",
                (status.value, self.clock().isoformat(), run_id, version),
            )
            if cursor.rowcount != 1:
                msg = f"run {run_id} is not at version {version}; refusing to update its status"
                raise ConcurrentRunUpdate(msg)
        updated = self.get_run(run_id)
        if updated is None:  # pragma: no cover - the update above proved it exists
            msg = f"run {run_id} vanished during a status update"
            raise PersistenceError(msg)
        return updated

    def set_paused(self, run_id: str, *, paused: bool) -> RunRecord:
        """Pause or resume a run. The scheduler refuses to advance a paused run.

        Raises:
            PersistenceError: No such run.
        """
        with self._transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET paused = ?, version = version + 1, updated_at = ?"
                " WHERE run_id = ?",
                (int(paused), self.clock().isoformat(), run_id),
            )
            if cursor.rowcount != 1:
                msg = f"no run {run_id}"
                raise PersistenceError(msg)
        updated = self.get_run(run_id)
        if updated is None:  # pragma: no cover - the update above proved it exists
            msg = f"run {run_id} vanished while being paused"
            raise PersistenceError(msg)
        return updated

    def delete_run(self, run_id: str) -> None:
        """Remove a run and everything under it.

        Only ever called by the demo reset, which refuses any run that is not
        ``LOCAL_FIXTURE``. The snapshot delete trigger fires only while the run row
        still exists, so the run row is removed first and the cascade then runs
        against an already-absent parent.

        Raises:
            PersistenceError: No such run.
        """
        if self.get_run(run_id) is None:
            msg = f"no run {run_id}"
            raise PersistenceError(msg)
        with self._transaction() as connection:
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def add_usage(self, run_id: str, *, usage: ModelUsage) -> RunRecord:
        """Fold model spend into a run's cumulative counters.

        Raises:
            PersistenceError: No such run.
        """
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT usage FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                msg = f"no run {run_id}"
                raise PersistenceError(msg)
            merged = _merge_usage(ModelUsage.model_validate(json.loads(row["usage"])), usage)
            connection.execute(
                "UPDATE runs SET usage = ?, updated_at = ? WHERE run_id = ?",
                (_dumps(merged.model_dump(mode="json")), self.clock().isoformat(), run_id),
            )
        updated = self.get_run(run_id)
        if updated is None:  # pragma: no cover - the update above proved it exists
            msg = f"run {run_id} vanished while recording usage"
            raise PersistenceError(msg)
        return updated

    # ----------------------------------------------------------------- locks

    def acquire_cycle_lock(
        self,
        run_id: str,
        *,
        cycle: int,
        invocation_id: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> CycleLock:
        """Take the right to advance ``run_id`` to ``cycle``.

        Raises:
            LockNotHeld: Another invocation holds an unexpired lock.
        """
        now = self.clock()
        lock = CycleLock(
            run_id=run_id,
            cycle=cycle,
            token=secrets.token_hex(16),
            invocation_id=invocation_id,
            acquired_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
        with self._transaction() as connection:
            row = connection.execute(
                "SELECT * FROM cycle_locks WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None:
                held = _lock_from_row(row)
                if not held.is_expired(now):
                    msg = (
                        f"invocation {held.invocation_id} holds the cycle lock on {run_id} "
                        f"for cycle {held.cycle} until {held.expires_at.isoformat()}"
                    )
                    raise LockNotHeld(msg)
            connection.execute(
                "INSERT INTO cycle_locks (run_id, schema_version, cycle, token, invocation_id,"
                " acquired_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id) DO UPDATE SET cycle = excluded.cycle,"
                " token = excluded.token, invocation_id = excluded.invocation_id,"
                " acquired_at = excluded.acquired_at, expires_at = excluded.expires_at",
                (
                    run_id,
                    lock.schema_version,
                    cycle,
                    lock.token,
                    invocation_id,
                    lock.acquired_at.isoformat(),
                    lock.expires_at.isoformat(),
                ),
            )
        return lock

    def release_cycle_lock(self, run_id: str, *, token: str) -> None:
        """Release the lock, if this caller still holds it. Silent when it does not."""
        with self._transaction() as connection:
            connection.execute(
                "DELETE FROM cycle_locks WHERE run_id = ? AND token = ?", (run_id, token)
            )

    def get_cycle_lock(self, run_id: str) -> CycleLock | None:
        """The lock currently recorded for a run, expired or not."""
        row = self._connection.execute(
            "SELECT * FROM cycle_locks WHERE run_id = ?", (run_id,)
        ).fetchone()
        return None if row is None else _lock_from_row(row)

    # ------------------------------------------------------- prepared cycles

    def store_prepared_cycle(self, prepared: PreparedCycle) -> PreparedCycle:
        """Persist a staged cycle, or return the identical one already stored.

        Raises:
            PreparedCycleConflict: A different cycle is already prepared here.
        """
        sealed = prepared if prepared.content_hash else prepared.sealed()
        existing = self.get_prepared_cycle(sealed.run_id, cycle=sealed.cycle)
        if existing is not None:
            if not existing.matches(sealed):
                msg = (
                    f"cycle {sealed.cycle} of {sealed.run_id} is already prepared with "
                    f"different content ({existing.content_hash} vs {sealed.content_hash}); "
                    f"two invocations staged different experiments"
                )
                raise PreparedCycleConflict(msg)
            return existing
        now = self.clock().isoformat()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO prepared_cycles (run_id, cycle, schema_version, content_hash,"
                " invocation_id, committed, payload, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, 0, ?, ?, ?)",
                (
                    sealed.run_id,
                    sealed.cycle,
                    sealed.schema_version,
                    sealed.content_hash,
                    sealed.invocation_id,
                    _dumps(sealed.model_dump(mode="json")),
                    now,
                    now,
                ),
            )
        return sealed

    def get_prepared_cycle(self, run_id: str, *, cycle: int) -> PreparedCycle | None:
        """The staged cycle for ``cycle``, committed or not."""
        row = self._connection.execute(
            "SELECT payload FROM prepared_cycles WHERE run_id = ? AND cycle = ?", (run_id, cycle)
        ).fetchone()
        return None if row is None else PreparedCycle.model_validate(json.loads(row["payload"]))

    # ------------------------------------------------------------ the commit

    def commit_cycle(
        self, run_id: str, *, cycle: int, token: str, content_hash: str, version: int
    ) -> RunRecord:
        """Commit a prepared cycle: six snapshots, six states, one advance, one lock.

        The eleven checks and writes of STEP 3, in one transaction. Any failure rolls
        the whole thing back, so no partial cycle is ever visible to a reader.

        Raises:
            ConcurrentRunUpdate: The run moved, is not at ``cycle`` minus one, or is
                at a different version than the caller read.
            LockNotHeld: ``token`` is not the lock this run is holding.
            PersistenceError: No prepared cycle matches, or it is already committed
                with different content.
        """
        now = self.clock().isoformat()
        with self._transaction() as connection:
            run_row = connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if run_row is None:
                msg = f"no run {run_id}"
                raise PersistenceError(msg)
            run = _run_from_row(run_row)

            # 1-2. The run must be exactly where this commit expects it.
            if run.version != version:
                msg = (
                    f"run {run_id} is at version {run.version}, not {version}; "
                    f"another invocation advanced it"
                )
                raise ConcurrentRunUpdate(msg)
            if run.current_cycle + 1 != cycle:
                msg = (
                    f"run {run_id} is at cycle {run.current_cycle}; cycle {cycle} is not "
                    f"the next one and a run may not skip"
                )
                raise ConcurrentRunUpdate(msg)

            # 3. The lock must still be ours.
            lock_row = connection.execute(
                "SELECT * FROM cycle_locks WHERE run_id = ?", (run_id,)
            ).fetchone()
            if lock_row is None or lock_row["token"] != token:
                msg = f"the cycle lock on {run_id} is not held by this invocation"
                raise LockNotHeld(msg)

            # 4. The prepared cycle must be the one this caller staged.
            prepared_row = connection.execute(
                "SELECT * FROM prepared_cycles WHERE run_id = ? AND cycle = ?", (run_id, cycle)
            ).fetchone()
            if prepared_row is None:
                msg = f"cycle {cycle} of {run_id} has not been prepared"
                raise PersistenceError(msg)
            if prepared_row["content_hash"] != content_hash:
                msg = (
                    f"cycle {cycle} of {run_id} is prepared as {prepared_row['content_hash']}, "
                    f"not {content_hash}"
                )
                raise PreparedCycleConflict(msg)
            prepared = PreparedCycle.model_validate(json.loads(prepared_row["payload"]))

            # 5. Six immutable snapshot rows.
            for snapshot in prepared.snapshots:
                connection.execute(
                    "INSERT INTO cycle_snapshots (run_id, arm_id, cycle, schema_version,"
                    " snapshot_hash, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id,
                        snapshot.arm_id.value,
                        cycle,
                        snapshot.schema_version,
                        snapshot.snapshot_hash,
                        _dumps(snapshot.model_dump(mode="json")),
                        now,
                    ),
                )

            # 6. Six current-state rows.
            for arm_id, state in sorted(prepared.arm_states.items()):
                connection.execute(
                    "INSERT INTO arm_current_states (run_id, arm_id, schema_version, cycle,"
                    " state_hash, payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
                    " ON CONFLICT(run_id, arm_id) DO UPDATE SET cycle = excluded.cycle,"
                    " state_hash = excluded.state_hash, payload = excluded.payload,"
                    " updated_at = excluded.updated_at",
                    (
                        run_id,
                        arm_id,
                        state.schema_version,
                        cycle,
                        state.state_hash,
                        _dumps(state.model_dump(mode="json")),
                        now,
                        now,
                    ),
                )

            # 7. The prepared cycle is spent.
            connection.execute(
                "UPDATE prepared_cycles SET committed = 1, updated_at = ?"
                " WHERE run_id = ? AND cycle = ?",
                (now, run_id, cycle),
            )

            # 8-9-11. One advance, the usage counters, and the terminal status.
            maximum = run.configuration.maximum_cycles
            status = RunStatus.COMPLETED if cycle >= maximum else RunStatus.RUNNING
            connection.execute(
                "UPDATE runs SET current_cycle = ?, status = ?, usage = ?,"
                " version = version + 1, updated_at = ? WHERE run_id = ? AND version = ?",
                (
                    cycle,
                    status.value,
                    _dumps(prepared.usage.model_dump(mode="json")),
                    now,
                    run_id,
                    version,
                ),
            )

            # 10. The lock is released by the same transaction that used it.
            connection.execute(
                "DELETE FROM cycle_locks WHERE run_id = ? AND token = ?", (run_id, token)
            )

        committed = self.get_run(run_id)
        if committed is None:  # pragma: no cover - the transaction above proved it exists
            msg = f"run {run_id} vanished during a cycle commit"
            raise PersistenceError(msg)
        return committed

    # ------------------------------------------------------------- arm state

    def seed_arm_state(self, run_id: str, *, arm_id: ArmId, state: MemoryState) -> None:
        """Install one arm's starting state, before any cycle exists.

        Raises:
            PersistenceError: The arm already has a state; seeds are installed once.
        """
        now = self.clock().isoformat()
        try:
            with self._transaction() as connection:
                connection.execute(
                    "INSERT INTO arm_current_states (run_id, arm_id, schema_version, cycle,"
                    " state_hash, payload, created_at, updated_at) VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        run_id,
                        arm_id.value,
                        state.schema_version,
                        state.state_hash,
                        _dumps(state.model_dump(mode="json")),
                        now,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            msg = f"{arm_id.value} of {run_id} already has a state; seeds are installed once"
            raise PersistenceError(msg) from exc

    def get_current_arm_state(self, run_id: str, *, arm_id: ArmId) -> MemoryState | None:
        """One arm's current memory, as of the last committed cycle."""
        row = self._connection.execute(
            "SELECT payload FROM arm_current_states WHERE run_id = ? AND arm_id = ?",
            (run_id, arm_id.value),
        ).fetchone()
        return None if row is None else MemoryState.model_validate(json.loads(row["payload"]))

    def get_all_current_arm_states(self, run_id: str) -> dict[str, MemoryState]:
        """Every arm's current memory, keyed by arm identifier."""
        rows = self._connection.execute(
            "SELECT arm_id, payload FROM arm_current_states WHERE run_id = ? ORDER BY arm_id",
            (run_id,),
        )
        return {
            row["arm_id"]: MemoryState.model_validate(json.loads(row["payload"])) for row in rows
        }

    # ------------------------------------------------------------- snapshots

    def get_cycle_snapshot(
        self, run_id: str, *, arm_id: ArmId, cycle: int
    ) -> ArmCycleSnapshot | None:
        """One committed arm-cycle record."""
        row = self._connection.execute(
            "SELECT payload FROM cycle_snapshots WHERE run_id = ? AND arm_id = ? AND cycle = ?",
            (run_id, arm_id.value, cycle),
        ).fetchone()
        return None if row is None else _snapshot(row)

    def list_cycle_snapshots(self, run_id: str, *, cycle: int) -> tuple[ArmCycleSnapshot, ...]:
        """Every arm's record for one committed cycle."""
        rows = self._connection.execute(
            "SELECT payload FROM cycle_snapshots WHERE run_id = ? AND cycle = ? ORDER BY arm_id",
            (run_id, cycle),
        )
        return tuple(_snapshot(row) for row in rows)

    def list_arm_snapshots(self, run_id: str, *, arm_id: ArmId) -> tuple[ArmCycleSnapshot, ...]:
        """One arm's records for every committed cycle, in cycle order."""
        rows = self._connection.execute(
            "SELECT payload FROM cycle_snapshots WHERE run_id = ? AND arm_id = ? ORDER BY cycle",
            (run_id, arm_id.value),
        )
        return tuple(_snapshot(row) for row in rows)

    def list_all_snapshots(self, run_id: str) -> tuple[ArmCycleSnapshot, ...]:
        """Every committed record for a run, in cycle then arm order."""
        rows = self._connection.execute(
            "SELECT payload FROM cycle_snapshots WHERE run_id = ? ORDER BY cycle, arm_id",
            (run_id,),
        )
        return tuple(_snapshot(row) for row in rows)

    def list_completed_cycles(self, run_id: str) -> tuple[int, ...]:
        """Every cycle number that has been committed, ascending."""
        rows = self._connection.execute(
            "SELECT DISTINCT cycle FROM cycle_snapshots WHERE run_id = ? ORDER BY cycle", (run_id,)
        )
        return tuple(int(row["cycle"]) for row in rows)

    # ------------------------------------------------------------ interviews

    def store_interview(self, interview: StoredInterview) -> StoredInterview:
        """Persist one checkpoint interview.

        Raises:
            PersistenceError: A different interview is already stored for this arm
                and cycle. An interview is a measurement and is not revised.
        """
        sealed = interview if interview.record_hash else interview.sealed()
        existing = self._interview(sealed.run_id, sealed.arm_id.value, sealed.cycle)
        if existing is not None:
            if existing.record_hash != sealed.record_hash:
                msg = (
                    f"{sealed.arm_id.value} was already interviewed at cycle {sealed.cycle}; "
                    f"an interview is a measurement and cannot be revised"
                )
                raise PersistenceError(msg)
            return existing
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO interviews (run_id, arm_id, cycle, schema_version, record_hash,"
                " input_state_hash, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    sealed.run_id,
                    sealed.arm_id.value,
                    sealed.cycle,
                    sealed.schema_version,
                    sealed.record_hash,
                    sealed.input_state_hash,
                    _dumps(sealed.model_dump(mode="json")),
                    self.clock().isoformat(),
                ),
            )
        return sealed

    def _interview(self, run_id: str, arm_id: str, cycle: int) -> StoredInterview | None:
        row = self._connection.execute(
            "SELECT payload FROM interviews WHERE run_id = ? AND arm_id = ? AND cycle = ?",
            (run_id, arm_id, cycle),
        ).fetchone()
        return None if row is None else StoredInterview.model_validate(json.loads(row["payload"]))

    def get_interviews(
        self, run_id: str, *, cycle: int | None = None, arm_id: ArmId | None = None
    ) -> tuple[StoredInterview, ...]:
        """Stored interviews, narrowed by cycle and arm when given."""
        clauses = ["run_id = ?"]
        parameters: list[Any] = [run_id]
        if cycle is not None:
            clauses.append("cycle = ?")
            parameters.append(cycle)
        if arm_id is not None:
            clauses.append("arm_id = ?")
            parameters.append(arm_id.value)
        rows = self._connection.execute(
            f"SELECT payload FROM interviews WHERE {' AND '.join(clauses)} ORDER BY cycle, arm_id",  # noqa: S608 - clauses are literals, values are bound
            parameters,
        )
        return tuple(StoredInterview.model_validate(json.loads(row["payload"])) for row in rows)

    # --------------------------------------------------------------- metrics

    def store_metric(self, metric: MetricEvidence) -> MetricEvidence:
        """Persist one scored metric with its evidence."""
        now = self.clock().isoformat()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO metrics (run_id, metric_name, arm_id, cycle, schema_version, value,"
                " payload, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id, metric_name, arm_id, cycle) DO UPDATE SET"
                " value = excluded.value, payload = excluded.payload,"
                " updated_at = excluded.updated_at",
                (
                    metric.run_id,
                    metric.metric_name,
                    metric.arm_id.value,
                    metric.cycle,
                    metric.schema_version,
                    metric.value,
                    _dumps(metric.model_dump(mode="json")),
                    now,
                    now,
                ),
            )
        return metric

    def get_metrics(
        self,
        run_id: str,
        *,
        metric_name: str | None = None,
        arm_id: ArmId | None = None,
        cycle: int | None = None,
    ) -> tuple[MetricEvidence, ...]:
        """Stored metrics, narrowed by name, arm, and cycle when given."""
        clauses = ["run_id = ?"]
        parameters: list[Any] = [run_id]
        for column, value in (
            ("metric_name", metric_name),
            ("arm_id", arm_id.value if arm_id is not None else None),
            ("cycle", cycle),
        ):
            if value is not None:
                clauses.append(f"{column} = ?")
                parameters.append(value)
        rows = self._connection.execute(
            f"SELECT payload FROM metrics WHERE {' AND '.join(clauses)}"  # noqa: S608 - clauses are literals, values are bound
            " ORDER BY metric_name, cycle, arm_id",
            parameters,
        )
        return tuple(MetricEvidence.model_validate(json.loads(row["payload"])) for row in rows)

    # ------------------------------------------------------------ embeddings

    def store_embedding(self, run_id: str, *, key: str, record: Mapping[str, object]) -> None:
        """Persist one embedding under a caller-chosen key."""
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO embeddings (run_id, key, schema_version, payload, created_at)"
                " VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT(run_id, key) DO UPDATE SET payload = excluded.payload",
                (run_id, key, _dumps(dict(record)), self.clock().isoformat()),
            )

    def get_embedding(self, run_id: str, *, key: str) -> dict[str, object] | None:
        """The embedding stored under ``key``, or None."""
        row = self._connection.execute(
            "SELECT payload FROM embeddings WHERE run_id = ? AND key = ?", (run_id, key)
        ).fetchone()
        loaded: dict[str, object] | None = None if row is None else json.loads(row["payload"])
        return loaded

    # ---------------------------------------------------------- token counts

    def store_token_count(self, *, counter_version: str, text_hash: str, tokens: int) -> None:
        """Cache one exact token count, so a re-run does not re-count."""
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO token_counts (counter_version, text_hash, schema_version, tokens,"
                " created_at) VALUES (?, ?, 1, ?, ?)"
                " ON CONFLICT(counter_version, text_hash) DO NOTHING",
                (counter_version, text_hash, tokens, self.clock().isoformat()),
            )

    def get_token_count(self, *, counter_version: str, text_hash: str) -> int | None:
        """The cached count for this counter and text, or None."""
        row = self._connection.execute(
            "SELECT tokens FROM token_counts WHERE counter_version = ? AND text_hash = ?",
            (counter_version, text_hash),
        ).fetchone()
        return None if row is None else int(row["tokens"])

    # -------------------------------------------------------------- analysis

    def store_analysis_status(self, status: AnalysisStatus) -> AnalysisStatus:
        """Record how far one analysis has got."""
        now = self.clock().isoformat()
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO analysis_status (run_id, analysis_name, schema_version,"
                " metric_version, completed_cycles, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id, analysis_name) DO UPDATE SET"
                " metric_version = excluded.metric_version,"
                " completed_cycles = excluded.completed_cycles, updated_at = excluded.updated_at",
                (
                    status.run_id,
                    status.analysis_name,
                    status.schema_version,
                    str(status.metric_version),
                    _dumps(list(status.completed_cycles)),
                    now,
                    now,
                ),
            )
        return status

    def get_analysis_status(self, run_id: str, *, analysis_name: str) -> AnalysisStatus | None:
        """How far one analysis has got, or None if it has not started."""
        row = self._connection.execute(
            "SELECT * FROM analysis_status WHERE run_id = ? AND analysis_name = ?",
            (run_id, analysis_name),
        ).fetchone()
        if row is None:
            return None
        return AnalysisStatus(
            run_id=row["run_id"],
            analysis_name=row["analysis_name"],
            metric_version=row["metric_version"],
            completed_cycles=tuple(json.loads(row["completed_cycles"])),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    # ---------------------------------------------------------------- export

    def store_export_manifest(self, manifest: ExportManifestRecord) -> ExportManifestRecord:
        """Record one completed export."""
        with self._transaction() as connection:
            connection.execute(
                "INSERT INTO export_manifests (run_id, export_id, schema_version, run_kind,"
                " directory, payload, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(run_id, export_id) DO UPDATE SET payload = excluded.payload,"
                " directory = excluded.directory",
                (
                    manifest.run_id,
                    manifest.export_id,
                    manifest.schema_version,
                    manifest.run_kind.value,
                    manifest.directory,
                    _dumps(manifest.model_dump(mode="json")),
                    manifest.created_at.isoformat(),
                ),
            )
        return manifest

    def get_export_manifest(self, run_id: str, *, export_id: str) -> ExportManifestRecord | None:
        """One export's manifest, or None."""
        row = self._connection.execute(
            "SELECT payload FROM export_manifests WHERE run_id = ? AND export_id = ?",
            (run_id, export_id),
        ).fetchone()
        if row is None:
            return None
        return ExportManifestRecord.model_validate(json.loads(row["payload"]))

    def list_export_manifests(self, run_id: str) -> tuple[ExportManifestRecord, ...]:
        """Every export recorded for a run, newest first."""
        rows = self._connection.execute(
            "SELECT payload FROM export_manifests WHERE run_id = ? ORDER BY created_at DESC",
            (run_id,),
        )
        return tuple(
            ExportManifestRecord.model_validate(json.loads(row["payload"])) for row in rows
        )


def _merge_usage(previous: ModelUsage, addition: ModelUsage) -> ModelUsage:
    """Add one tally to another, keeping the ledger in the order calls were made."""
    roles = dict(previous.calls_by_role)
    for role, count in addition.calls_by_role.items():
        roles[role] = roles.get(role, 0) + count
    return ModelUsage(
        calls_by_role=roles,
        ledger=(*previous.ledger, *addition.ledger),
        total_calls=previous.total_calls + addition.total_calls,
        failed_calls=previous.failed_calls + addition.failed_calls,
        simulated_calls=previous.simulated_calls + addition.simulated_calls,
        input_tokens=previous.input_tokens + addition.input_tokens,
        output_tokens=previous.output_tokens + addition.output_tokens,
        retries=previous.retries + addition.retries,
    )


# ------------------------------------------------------------------- row maps


def _snapshot(row: sqlite3.Row) -> ArmCycleSnapshot:
    return ArmCycleSnapshot.model_validate(json.loads(row["payload"]))


def _run_from_row(row: sqlite3.Row) -> RunRecord:
    from attention_sink.pilot.configuration import PilotRunConfiguration, RunKind

    return RunRecord(
        run_id=row["run_id"],
        run_kind=RunKind(row["run_kind"]),
        status=RunStatus(row["status"]),
        current_cycle=int(row["current_cycle"]),
        version=int(row["version"]),
        paused=bool(row["paused"]),
        configuration=PilotRunConfiguration.model_validate(json.loads(row["configuration"])),
        usage=ModelUsage.model_validate(json.loads(row["usage"])),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def _lock_from_row(row: sqlite3.Row) -> CycleLock:
    return CycleLock(
        run_id=row["run_id"],
        cycle=int(row["cycle"]),
        token=row["token"],
        invocation_id=row["invocation_id"],
        acquired_at=datetime.fromisoformat(row["acquired_at"]),
        expires_at=datetime.fromisoformat(row["expires_at"]),
    )
