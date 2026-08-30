"""The DynamoDB adapter: one table, one transaction, the same guarantees as SQLite.

Implements :class:`~attention_sink.pilot.repositories.PilotRepository`, the same
protocol :class:`~attention_sink.persistence.SqliteRepository` implements. Nothing
above the adapter line changes when a process swaps one for the other, and
``tests/integration/test_dynamodb.py`` re-proves the SQLite guarantees against this
class rather than trusting that they carried over.

Three things here are load-bearing.

**The cycle commit is one ``TransactWriteItems``.** Fourteen writes -- six snapshots,
six arm states, the prepared cycle, the run head -- conditioned on the run's version,
its current cycle, the lock token, and the prepared cycle's content hash. DynamoDB
either applies all of them or none, so five arms that advanced and one that did not
is not a state this store can reach.

**The lock lives on the run's own item.** A separate lock item would need its own
transaction entry and its own race; as attributes of ``RUN#{id} / META`` the checks
"the run is where I left it" and "the lock is still mine" are one condition
expression on one item.

**Immutable records are written with ``attribute_not_exists``.** SQLite refuses an
``UPDATE`` of a snapshot with a trigger; DynamoDB has no triggers, so the equivalent
is a condition that a ``Put`` may only create. A rewrite fails rather than replacing
a committed record.

Item sizes are known rather than assumed. A snapshot of the pilot's seed world runs
about 20 KB and an arm state about 25 KB, both far inside the 400 KB item ceiling. A
prepared cycle carries six of each at once and would run near 280 KB, so its payload
is stored compressed -- see :func:`_pack`. The whole commit transaction is about
275 KB against a 4 MB limit.
"""

from __future__ import annotations

import json
import secrets
import zlib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from attention_sink.aws import keys
from attention_sink.domain import ArmId, MemoryState, MetricEvidence
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

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_dynamodb.type_defs import (
        AttributeValueTypeDef,
        TransactWriteItemTypeDef,
    )

__all__ = [
    "ARM_SNAPSHOT_INDEX",
    "DEFAULT_LOCK_TTL_SECONDS",
    "RUN_LISTING_INDEX",
    "SCHEMA_VERSION",
    "DynamoRepository",
]

SCHEMA_VERSION = 1
"""Written on every item. Not the run's optimistic-concurrency ``version``: this one
says how to read the item, that one says whether somebody else moved first."""

RUN_LISTING_INDEX = "GSI1"
"""Newest-first run listing, and one arm's snapshots in cycle order.

One sparse index serves both because only run heads and snapshots carry the index
keys, and the two partitions they use cannot collide: ``RUNS`` against
``RUN#{run}#ARM#{arm}``."""

ARM_SNAPSHOT_INDEX = RUN_LISTING_INDEX
"""The same index, named for its second access pattern where that reads better."""

DEFAULT_LOCK_TTL_SECONDS = 300
"""How long a cycle lease lasts. Comfortably longer than one six-arm cycle, short
enough that a Lambda killed at its timeout unwedges the run within five minutes."""

_MAX_USAGE_ATTEMPTS = 5
"""Optimistic retries when folding spend into a run's counters. Contention here is
two invocations recording checkpoint interviews, which is rare and short."""

_COMPRESSION_LEVEL = 6


def _now() -> datetime:
    return datetime.now(UTC)


def _dumps(value: Any) -> str:
    """Serialise a record for storage, sorted so two writes of one record match."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _pack(value: Any) -> bytes:
    """Compress a record that would otherwise crowd the 400 KB item ceiling.

    Only the prepared cycle uses this. It holds six snapshots and six arm states at
    once, and the same memory text appears in the before-set, the after-set, and the
    state, so it compresses by roughly an order of magnitude. Everything else is
    stored as readable JSON, because being able to look at an item in the console is
    worth more than the bytes.
    """
    return zlib.compress(_dumps(value).encode("utf-8"), _COMPRESSION_LEVEL)


def _unpack(raw: bytes) -> Any:
    return json.loads(zlib.decompress(raw).decode("utf-8"))


def _s(value: str) -> AttributeValueTypeDef:
    return {"S": value}


def _n(value: int) -> AttributeValueTypeDef:
    return {"N": str(value)}


def _b(value: bytes) -> AttributeValueTypeDef:
    return {"B": value}


def _bool(value: bool) -> AttributeValueTypeDef:
    return {"BOOL": value}


def _text(item: Mapping[str, Any], name: str) -> str:
    value: str = item[name]["S"]
    return value


def _binary(item: Mapping[str, Any], name: str) -> bytes:
    raw: Any = item[name]["B"]
    return raw.read() if hasattr(raw, "read") else bytes(raw)


@dataclass
class DynamoRepository:
    """A transactional store for one pilot table.

    Stateless between calls and safe to construct per Lambda invocation: everything
    it needs is the table name and a client, and every method is one or a few
    requests with no session of its own.
    """

    table_name: str
    client: DynamoDBClient
    embedding_model_id: str = "unconfigured-embedding-model"
    """Which model's vectors this repository caches.

    Part of the cache partition, so switching embedding models cannot serve vectors
    produced by the previous one. Defaulted rather than required because a process
    that never embeds -- the read API, the cycle runner -- has no reason to know it.
    """

    clock: Any = _now
    lock_ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS
    _consistent_reads: bool = field(default=True, repr=False)
    """Every read is strongly consistent.

    A cycle decides what to write from what it just read, and an eventually
    consistent read of a run's head would let two invocations both believe they were
    about to commit cycle 7. The cost is real and the alternative is a wrong
    experiment.
    """

    # -------------------------------------------------------------- primitives

    def _get(self, pk: str, sk: str) -> dict[str, Any] | None:
        response = self.client.get_item(
            TableName=self.table_name,
            Key={"PK": _s(pk), "SK": _s(sk)},
            ConsistentRead=self._consistent_reads,
        )
        item = response.get("Item")
        return dict(item) if item else None

    def _put(
        self,
        item: Mapping[str, AttributeValueTypeDef],
        *,
        condition: str | None = None,
        names: Mapping[str, str] | None = None,
        values: Mapping[str, AttributeValueTypeDef] | None = None,
    ) -> None:
        request: dict[str, Any] = {"TableName": self.table_name, "Item": dict(item)}
        if condition is not None:
            request["ConditionExpression"] = condition
        if names is not None:
            request["ExpressionAttributeNames"] = dict(names)
        if values is not None:
            request["ExpressionAttributeValues"] = dict(values)
        self.client.put_item(**request)

    def _query(
        self,
        pk: str,
        *,
        prefix: str | None = None,
        index: str | None = None,
        projection: str | None = None,
        forward: bool = True,
    ) -> Iterator[dict[str, Any]]:
        """Every item in one partition, paged to the end.

        A generator rather than a list because the caller usually parses each item
        into a Pydantic model, and materialising the raw pages first would hold two
        copies of a run's snapshots in a Lambda's memory at once.
        """
        key_names = ("GSI1PK", "GSI1SK") if index else ("PK", "SK")
        expression = "#pk = :pk" if prefix is None else "#pk = :pk AND begins_with(#sk, :sk)"
        names = {"#pk": key_names[0]} | ({} if prefix is None else {"#sk": key_names[1]})
        values: dict[str, AttributeValueTypeDef] = {":pk": _s(pk)}
        if prefix is not None:
            values[":sk"] = _s(prefix)
        request: dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": expression,
            "ExpressionAttributeNames": names,
            "ExpressionAttributeValues": values,
            "ScanIndexForward": forward,
        }
        if index is not None:
            request["IndexName"] = index
        else:
            request["ConsistentRead"] = self._consistent_reads
        if projection is not None:
            request["ProjectionExpression"] = projection
            request["ExpressionAttributeNames"] = names | {"#sk": key_names[1]}
        while True:
            response = self.client.query(**request)
            yield from (dict(item) for item in response.get("Items", ()))
            last = response.get("LastEvaluatedKey")
            if not last:
                return
            request["ExclusiveStartKey"] = last

    # -------------------------------------------------------------------- runs

    def create_run(self, record: RunRecord) -> RunRecord:
        """Insert a new run.

        Raises:
            PersistenceError: A run with that identifier already exists.
        """
        try:
            self._put(
                self._run_item(record), condition="attribute_not_exists(#pk)", names={"#pk": "PK"}
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            msg = f"run {record.run_id} already exists"
            raise PersistenceError(msg) from exc
        return record

    def _run_item(self, record: RunRecord) -> dict[str, AttributeValueTypeDef]:
        """The run's head, with its listing keys and no lock.

        A newly created run holds no lock, so the lock attributes are simply absent.
        That is what ``acquire_cycle_lock``'s ``attribute_not_exists`` condition
        tests, and it is why a lock is released by removing attributes rather than
        by writing a null.
        """
        return {
            "PK": _s(keys.run_pk(record.run_id)),
            "SK": _s(keys.META_SK),
            "GSI1PK": _s(keys.RUNS_PARTITION),
            "GSI1SK": _s(keys.run_sort_key(record.created_at.isoformat(), record.run_id)),
            "schema_version": _n(SCHEMA_VERSION),
            "record_type": _s("run"),
            "run_id": _s(record.run_id),
            "run_kind": _s(record.run_kind.value),
            "status": _s(record.status.value),
            "current_cycle": _n(record.current_cycle),
            "version": _n(record.version),
            "paused": _bool(record.paused),
            "usage_seq": _n(0),
            "configuration": _s(_dumps(record.configuration.model_dump(mode="json"))),
            "usage": _s(_dumps(record.usage.model_dump(mode="json"))),
            "created_at": _s(record.created_at.isoformat()),
            "updated_at": _s(record.updated_at.isoformat()),
        }

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return one run's head, or None when it does not exist."""
        item = self._get(keys.run_pk(run_id), keys.META_SK)
        return None if item is None else _run_from_item(item)

    def list_runs(self) -> tuple[RunRecord, ...]:
        """Every run, newest first.

        A ``Query`` on the listing index, never a ``Scan``. The public API calls this
        on every visit to the run list, and a table scan on a public path is a cost
        that grows with data nobody asked for.
        """
        items = self._query(keys.RUNS_PARTITION, index=RUN_LISTING_INDEX, forward=False)
        return tuple(_run_from_item(item) for item in items)

    def update_run_status(self, run_id: str, *, status: RunStatus, version: int) -> RunRecord:
        """Move a run to ``status``, if it is still at ``version``.

        Raises:
            ConcurrentRunUpdate: The run has moved on, or does not exist.
        """
        try:
            response = self.client.update_item(
                TableName=self.table_name,
                Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.META_SK)},
                UpdateExpression=("SET #status = :status, #version = :next, #updated = :now"),
                ConditionExpression="attribute_exists(#pk) AND #version = :expected",
                ExpressionAttributeNames={
                    "#pk": "PK",
                    "#status": "status",
                    "#version": "version",
                    "#updated": "updated_at",
                },
                ExpressionAttributeValues={
                    ":status": _s(status.value),
                    ":next": _n(version + 1),
                    ":expected": _n(version),
                    ":now": _s(self.clock().isoformat()),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            msg = f"run {run_id} is not at version {version}; refusing to update its status"
            raise ConcurrentRunUpdate(msg) from exc
        return _run_from_item(dict(response["Attributes"]))

    def set_paused(self, run_id: str, *, paused: bool) -> RunRecord:
        """Pause or resume a run. The scheduler refuses to advance a paused run.

        Raises:
            PersistenceError: No such run.
        """
        try:
            response = self.client.update_item(
                TableName=self.table_name,
                Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.META_SK)},
                UpdateExpression=(
                    "SET #paused = :paused, #version = #version + :one, #updated = :now"
                ),
                ConditionExpression="attribute_exists(#pk)",
                ExpressionAttributeNames={
                    "#pk": "PK",
                    "#paused": "paused",
                    "#version": "version",
                    "#updated": "updated_at",
                },
                ExpressionAttributeValues={
                    ":paused": _bool(paused),
                    ":one": _n(1),
                    ":now": _s(self.clock().isoformat()),
                },
                ReturnValues="ALL_NEW",
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            msg = f"no run {run_id}"
            raise PersistenceError(msg) from exc
        return _run_from_item(dict(response["Attributes"]))

    def add_usage(self, run_id: str, *, usage: ModelUsage) -> RunRecord:
        """Fold model spend into a run's cumulative counters.

        Read, merge, write under a condition on a counter that only this method
        touches, and retry a bounded number of times. Not a blind write: a checkpoint
        that lost its interviewer calls to a concurrent update would understate what
        the run actually spent, and a spend counter that undercounts is worse than
        one that fails.

        Raises:
            PersistenceError: No such run, or the counters would not settle.
        """
        for _ in range(_MAX_USAGE_ATTEMPTS):
            item = self._get(keys.run_pk(run_id), keys.META_SK)
            if item is None:
                msg = f"no run {run_id}"
                raise PersistenceError(msg)
            sequence = int(item["usage_seq"]["N"])
            merged = merge_usage(ModelUsage.model_validate(json.loads(_text(item, "usage"))), usage)
            try:
                response = self.client.update_item(
                    TableName=self.table_name,
                    Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.META_SK)},
                    UpdateExpression="SET #usage = :usage, #seq = :next, #updated = :now",
                    ConditionExpression="#seq = :expected",
                    ExpressionAttributeNames={
                        "#usage": "usage",
                        "#seq": "usage_seq",
                        "#updated": "updated_at",
                    },
                    ExpressionAttributeValues={
                        ":usage": _s(_dumps(merged.model_dump(mode="json"))),
                        ":next": _n(sequence + 1),
                        ":expected": _n(sequence),
                        ":now": _s(self.clock().isoformat()),
                    },
                    ReturnValues="ALL_NEW",
                )
            except ClientError as exc:
                if _code(exc) != "ConditionalCheckFailedException":
                    raise
                continue
            return _run_from_item(dict(response["Attributes"]))
        msg = f"run {run_id} usage counters were contended {_MAX_USAGE_ATTEMPTS} times running"
        raise PersistenceError(msg)

    # ------------------------------------------------------------------- locks

    def acquire_cycle_lock(
        self,
        run_id: str,
        *,
        cycle: int,
        invocation_id: str,
        ttl_seconds: int = DEFAULT_LOCK_TTL_SECONDS,
    ) -> CycleLock:
        """Take the right to advance ``run_id`` to ``cycle``.

        One conditional update. An absent or expired lease is replaced; an unexpired
        one held by somebody else is refused, and nothing is written.

        Raises:
            LockNotHeld: Another invocation holds an unexpired lock.
            PersistenceError: No such run.
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
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.META_SK)},
                UpdateExpression=(
                    "SET lock_token = :token, lock_cycle = :cycle,"
                    " lock_invocation_id = :invocation, lock_acquired_at = :acquired,"
                    " lock_expires_at = :expires"
                ),
                ConditionExpression=(
                    "attribute_exists(#pk) AND"
                    " (attribute_not_exists(lock_token) OR lock_expires_at <= :now)"
                ),
                ExpressionAttributeNames={"#pk": "PK"},
                ExpressionAttributeValues={
                    ":token": _s(lock.token),
                    ":cycle": _n(cycle),
                    ":invocation": _s(invocation_id),
                    ":acquired": _s(lock.acquired_at.isoformat()),
                    ":expires": _s(lock.expires_at.isoformat()),
                    ":now": _s(now.isoformat()),
                },
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            raise self._refused_lock(run_id, now) from exc
        return lock

    def _refused_lock(self, run_id: str, now: datetime) -> PersistenceError:
        """Explain a refused lock by looking at what is actually there.

        The condition covers two different failures -- no such run, and somebody else
        holds the lease -- and a single message covering both would send a reader
        looking for the wrong problem.
        """
        held = self.get_cycle_lock(run_id)
        if held is None:
            return PersistenceError(f"no run {run_id}")
        return LockNotHeld(
            f"invocation {held.invocation_id} holds the cycle lock on {run_id} "
            f"for cycle {held.cycle} until {held.expires_at.isoformat()}"
            f"{'' if not held.is_expired(now) else ' (expired)'}"
        )

    def release_cycle_lock(self, run_id: str, *, token: str) -> None:
        """Release the lock, if this caller still holds it. Silent when it does not."""
        try:
            self.client.update_item(
                TableName=self.table_name,
                Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.META_SK)},
                UpdateExpression=(
                    "REMOVE lock_token, lock_cycle, lock_invocation_id,"
                    " lock_acquired_at, lock_expires_at"
                ),
                ConditionExpression="lock_token = :token",
                ExpressionAttributeValues={":token": _s(token)},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise

    def get_cycle_lock(self, run_id: str) -> CycleLock | None:
        """The lock currently recorded for a run, expired or not."""
        item = self._get(keys.run_pk(run_id), keys.META_SK)
        if item is None or "lock_token" not in item:
            return None
        return CycleLock(
            run_id=run_id,
            cycle=int(item["lock_cycle"]["N"]),
            token=_text(item, "lock_token"),
            invocation_id=_text(item, "lock_invocation_id"),
            acquired_at=datetime.fromisoformat(_text(item, "lock_acquired_at")),
            expires_at=datetime.fromisoformat(_text(item, "lock_expires_at")),
        )

    # --------------------------------------------------------- prepared cycles

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
        self._put(
            {
                "PK": _s(keys.run_pk(sealed.run_id)),
                "SK": _s(keys.prepared_sk(sealed.cycle)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("prepared_cycle"),
                "cycle": _n(sealed.cycle),
                "content_hash": _s(sealed.content_hash),
                "invocation_id": _s(sealed.invocation_id),
                "committed": _bool(False),
                "payload": _b(_pack(sealed.model_dump(mode="json"))),
                "created_at": _s(now),
                "updated_at": _s(now),
            }
        )
        return sealed

    def get_prepared_cycle(self, run_id: str, *, cycle: int) -> PreparedCycle | None:
        """The staged cycle for ``cycle``, committed or not."""
        item = self._get(keys.run_pk(run_id), keys.prepared_sk(cycle))
        if item is None:
            return None
        return PreparedCycle.model_validate(_unpack(_binary(item, "payload")))

    # -------------------------------------------------------------- the commit

    def commit_cycle(
        self, run_id: str, *, cycle: int, token: str, content_hash: str, version: int
    ) -> RunRecord:
        """Commit a prepared cycle: six snapshots, six states, one advance, one lock.

        Checked first and conditioned again inside the transaction. The checks
        produce the message a reader needs; the conditions make the check binding
        against another invocation that moved in between.

        Raises:
            ConcurrentRunUpdate: The run moved, is not at ``cycle`` minus one, or is
                at a different version than the caller read.
            LockNotHeld: ``token`` is not the lock this run is holding.
            PreparedCycleConflict: The prepared cycle is not the one named.
            PersistenceError: No such run, or the cycle was never prepared.
        """
        run = self.get_run(run_id)
        if run is None:
            msg = f"no run {run_id}"
            raise PersistenceError(msg)
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
        held = self.get_cycle_lock(run_id)
        if held is None or held.token != token:
            msg = f"the cycle lock on {run_id} is not held by this invocation"
            raise LockNotHeld(msg)
        prepared = self.get_prepared_cycle(run_id, cycle=cycle)
        if prepared is None:
            msg = f"cycle {cycle} of {run_id} has not been prepared"
            raise PersistenceError(msg)
        if prepared.content_hash != content_hash:
            msg = (
                f"cycle {cycle} of {run_id} is prepared as {prepared.content_hash}, "
                f"not {content_hash}"
            )
            raise PreparedCycleConflict(msg)

        try:
            self.client.transact_write_items(
                TransactItems=self._commit_items(
                    run, prepared=prepared, cycle=cycle, token=token, version=version
                )
            )
        except ClientError as exc:
            if _code(exc) != "TransactionCanceledException":
                raise
            msg = (
                f"the commit of cycle {cycle} of {run_id} was cancelled; the run, its "
                f"lock, or its prepared cycle moved between the check and the write, "
                f"and nothing was written"
            )
            raise ConcurrentRunUpdate(msg) from exc

        committed = self.get_run(run_id)
        if committed is None:  # pragma: no cover - the transaction proved it exists
            msg = f"run {run_id} vanished during a cycle commit"
            raise PersistenceError(msg)
        return committed

    def _commit_items(
        self, run: RunRecord, *, prepared: PreparedCycle, cycle: int, token: str, version: int
    ) -> list[TransactWriteItemTypeDef]:
        """The fourteen writes one cycle commit makes, in one all-or-nothing list."""
        now = self.clock().isoformat()
        maximum = run.configuration.maximum_cycles
        status = RunStatus.COMPLETED if cycle >= maximum else RunStatus.RUNNING
        items: list[TransactWriteItemTypeDef] = [
            {
                "Update": {
                    "TableName": self.table_name,
                    "Key": {"PK": _s(keys.run_pk(run.run_id)), "SK": _s(keys.META_SK)},
                    "UpdateExpression": (
                        "SET current_cycle = :cycle, #status = :status, #usage = :usage,"
                        " #version = :next, updated_at = :now"
                        " REMOVE lock_token, lock_cycle, lock_invocation_id,"
                        " lock_acquired_at, lock_expires_at"
                    ),
                    "ConditionExpression": (
                        "#version = :expected AND current_cycle = :previous AND lock_token = :token"
                    ),
                    "ExpressionAttributeNames": {
                        "#status": "status",
                        "#usage": "usage",
                        "#version": "version",
                    },
                    "ExpressionAttributeValues": {
                        ":cycle": _n(cycle),
                        ":status": _s(status.value),
                        ":usage": _s(_dumps(prepared.usage.model_dump(mode="json"))),
                        ":next": _n(version + 1),
                        ":expected": _n(version),
                        ":previous": _n(cycle - 1),
                        ":token": _s(token),
                        ":now": _s(now),
                    },
                }
            }
        ]
        for snapshot in prepared.snapshots:
            items.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": self._snapshot_item(run.run_id, snapshot, now=now),
                        # No triggers here, so immutability is a condition: a Put
                        # that may only create cannot overwrite a committed record.
                        "ConditionExpression": "attribute_not_exists(#sk)",
                        "ExpressionAttributeNames": {"#sk": "SK"},
                    }
                }
            )
        for arm_id, state in sorted(prepared.arm_states.items()):
            items.append(
                {
                    "Put": {
                        "TableName": self.table_name,
                        "Item": self._state_item(run.run_id, arm_id, state, cycle=cycle, now=now),
                    }
                }
            )
        items.append(
            {
                "Update": {
                    "TableName": self.table_name,
                    "Key": {
                        "PK": _s(keys.run_pk(run.run_id)),
                        "SK": _s(keys.prepared_sk(cycle)),
                    },
                    "UpdateExpression": "SET committed = :true, updated_at = :now",
                    "ConditionExpression": "content_hash = :hash",
                    "ExpressionAttributeValues": {
                        ":true": _bool(True),
                        ":hash": _s(prepared.content_hash),
                        ":now": _s(now),
                    },
                }
            }
        )
        return items

    def _snapshot_item(
        self, run_id: str, snapshot: ArmCycleSnapshot, *, now: str
    ) -> dict[str, AttributeValueTypeDef]:
        return {
            "PK": _s(keys.run_pk(run_id)),
            "SK": _s(keys.snapshot_sk(snapshot.cycle, snapshot.arm_id.value)),
            "GSI1PK": _s(keys.arm_snapshot_index_pk(run_id, snapshot.arm_id.value)),
            "GSI1SK": _s(f"CYCLE#{snapshot.cycle:0{keys.CYCLE_DIGITS}d}"),
            "schema_version": _n(SCHEMA_VERSION),
            "record_type": _s("cycle_snapshot"),
            "cycle": _n(snapshot.cycle),
            "arm_id": _s(snapshot.arm_id.value),
            "snapshot_hash": _s(snapshot.snapshot_hash),
            "payload": _s(_dumps(snapshot.model_dump(mode="json"))),
            "created_at": _s(now),
        }

    def _state_item(
        self, run_id: str, arm_id: str, state: MemoryState, *, cycle: int, now: str
    ) -> dict[str, AttributeValueTypeDef]:
        return {
            "PK": _s(keys.run_pk(run_id)),
            "SK": _s(keys.arm_state_sk(arm_id)),
            "schema_version": _n(SCHEMA_VERSION),
            "record_type": _s("arm_state"),
            "arm_id": _s(arm_id),
            "cycle": _n(cycle),
            "state_hash": _s(state.state_hash),
            "payload": _s(_dumps(state.model_dump(mode="json"))),
            "updated_at": _s(now),
        }

    # --------------------------------------------------------------- arm state

    def seed_arm_state(self, run_id: str, *, arm_id: ArmId, state: MemoryState) -> None:
        """Install one arm's starting state, before any cycle exists.

        Raises:
            PersistenceError: The arm already has a state; seeds are installed once.
        """
        try:
            self._put(
                self._state_item(
                    run_id, arm_id.value, state, cycle=0, now=self.clock().isoformat()
                ),
                condition="attribute_not_exists(#sk)",
                names={"#sk": "SK"},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            msg = f"{arm_id.value} of {run_id} already has a state; seeds are installed once"
            raise PersistenceError(msg) from exc

    def get_current_arm_state(self, run_id: str, *, arm_id: ArmId) -> MemoryState | None:
        """One arm's current memory, as of the last committed cycle."""
        item = self._get(keys.run_pk(run_id), keys.arm_state_sk(arm_id.value))
        return None if item is None else _state(item)

    def get_all_current_arm_states(self, run_id: str) -> dict[str, MemoryState]:
        """Every arm's current memory, keyed by arm identifier."""
        return {
            _text(item, "arm_id"): _state(item)
            for item in self._query(keys.run_pk(run_id), prefix=keys.ARM_STATE_PREFIX)
        }

    # --------------------------------------------------------------- snapshots

    def get_cycle_snapshot(
        self, run_id: str, *, arm_id: ArmId, cycle: int
    ) -> ArmCycleSnapshot | None:
        """One committed arm-cycle record."""
        item = self._get(keys.run_pk(run_id), keys.snapshot_sk(cycle, arm_id.value))
        return None if item is None else _snapshot(item)

    def list_cycle_snapshots(self, run_id: str, *, cycle: int) -> tuple[ArmCycleSnapshot, ...]:
        """Every arm's record for one committed cycle, in arm order."""
        return tuple(
            _snapshot(item)
            for item in self._query(keys.run_pk(run_id), prefix=keys.snapshot_prefix(cycle))
        )

    def list_arm_snapshots(self, run_id: str, *, arm_id: ArmId) -> tuple[ArmCycleSnapshot, ...]:
        """One arm's records for every committed cycle, in cycle order.

        Served by the index, because the table's own sort key is cycle-major and one
        arm's history is therefore spread across every cycle prefix.
        """
        return tuple(
            _snapshot(item)
            for item in self._query(
                keys.arm_snapshot_index_pk(run_id, arm_id.value), index=ARM_SNAPSHOT_INDEX
            )
        )

    def list_completed_cycles(self, run_id: str) -> tuple[int, ...]:
        """Every cycle number that has been committed, ascending.

        Projects sort keys only. Reading whole snapshots to learn a list of integers
        would make the cheapest question the API answers the most expensive one.
        """
        cycles = {
            cycle
            for item in self._query(
                keys.run_pk(run_id), prefix=keys.snapshot_prefix(), projection="#sk"
            )
            if (cycle := keys.cycle_of_snapshot_sk(_text(item, "SK"))) is not None
        }
        return tuple(sorted(cycles))

    # -------------------------------------------------------------- interviews

    def store_interview(self, interview: StoredInterview) -> StoredInterview:
        """Persist one checkpoint interview. Re-storing an identical one is a no-op.

        Raises:
            PersistenceError: A different interview is already stored for this arm
                and cycle. An interview is a measurement and is not revised.
        """
        sealed = interview if interview.record_hash else interview.sealed()
        try:
            self._put(
                {
                    "PK": _s(keys.run_pk(sealed.run_id)),
                    "SK": _s(keys.interview_sk(sealed.cycle, sealed.arm_id.value)),
                    "schema_version": _n(SCHEMA_VERSION),
                    "record_type": _s("interview"),
                    "cycle": _n(sealed.cycle),
                    "arm_id": _s(sealed.arm_id.value),
                    "record_hash": _s(sealed.record_hash),
                    "input_state_hash": _s(sealed.input_state_hash),
                    "payload": _s(_dumps(sealed.model_dump(mode="json"))),
                    "created_at": _s(self.clock().isoformat()),
                },
                condition="attribute_not_exists(#sk) OR record_hash = :hash",
                names={"#sk": "SK"},
                values={":hash": _s(sealed.record_hash)},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            msg = (
                f"{sealed.arm_id.value} was already interviewed at cycle {sealed.cycle}; "
                f"an interview is a measurement and cannot be revised"
            )
            raise PersistenceError(msg) from exc
        return sealed

    def get_interviews(
        self, run_id: str, *, cycle: int | None = None, arm_id: ArmId | None = None
    ) -> tuple[StoredInterview, ...]:
        """Stored interviews, narrowed by cycle and arm when given."""
        items = self._query(keys.run_pk(run_id), prefix=keys.interview_prefix(cycle))
        stored = (
            StoredInterview.model_validate(json.loads(_text(item, "payload"))) for item in items
        )
        return tuple(i for i in stored if arm_id is None or i.arm_id is arm_id)

    # ----------------------------------------------------------------- metrics

    def store_metric(self, metric: MetricEvidence) -> MetricEvidence:
        """Persist one scored metric with its evidence."""
        self._put(
            {
                "PK": _s(keys.run_pk(metric.run_id)),
                "SK": _s(keys.metric_sk(metric.metric_name, metric.cycle, metric.arm_id.value)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("metric"),
                "metric_name": _s(metric.metric_name),
                "cycle": _n(metric.cycle),
                "arm_id": _s(metric.arm_id.value),
                "payload": _s(_dumps(metric.model_dump(mode="json"))),
                "updated_at": _s(self.clock().isoformat()),
            }
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
        prefix = keys.metric_prefix(metric_name, cycle if metric_name else None)
        items = self._query(keys.run_pk(run_id), prefix=prefix)
        found = (
            MetricEvidence.model_validate(json.loads(_text(item, "payload"))) for item in items
        )
        return tuple(
            metric
            for metric in found
            if (arm_id is None or metric.arm_id is arm_id)
            and (cycle is None or metric.cycle == cycle)
        )

    # -------------------------------------------------------------- embeddings

    def store_embedding(self, run_id: str, *, key: str, record: Mapping[str, object]) -> None:
        """Persist one embedding, partitioned by the model that produced it.

        Not partitioned by run. A vector is a function of a model and a piece of
        text, so two runs of one protocol would otherwise pay twice for numbers that
        are identical by construction. ``run_id`` is recorded on the item as the run
        that first paid for it, and is not part of the key.
        """
        del run_id
        self._put(
            {
                "PK": _s(keys.embedding_pk(self.embedding_model_id)),
                "SK": _s(keys.cache_sk(key)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("embedding"),
                "model_id": _s(self.embedding_model_id),
                "payload": _s(_dumps(dict(record))),
                "created_at": _s(self.clock().isoformat()),
            }
        )

    def get_embedding(self, run_id: str, *, key: str) -> dict[str, object] | None:
        """The embedding stored under ``key`` for this repository's model, or None."""
        del run_id
        item = self._get(keys.embedding_pk(self.embedding_model_id), keys.cache_sk(key))
        if item is None:
            return None
        loaded: dict[str, object] = json.loads(_text(item, "payload"))
        return loaded

    # ------------------------------------------------------------ token counts

    def store_token_count(self, *, counter_version: str, text_hash: str, tokens: int) -> None:
        """Cache one exact token count, so a re-run does not re-count."""
        self._put(
            {
                "PK": _s(keys.token_pk(counter_version)),
                "SK": _s(keys.cache_sk(text_hash)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("token_count"),
                "counter_version": _s(counter_version),
                "tokens": _n(tokens),
                "created_at": _s(self.clock().isoformat()),
            }
        )

    def get_token_count(self, *, counter_version: str, text_hash: str) -> int | None:
        """The cached count for this counter and text, or None."""
        item = self._get(keys.token_pk(counter_version), keys.cache_sk(text_hash))
        return None if item is None else int(item["tokens"]["N"])

    # ---------------------------------------------------------------- analysis

    def store_analysis_status(self, status: AnalysisStatus) -> AnalysisStatus:
        """Record how far one analysis has got."""
        self._put(
            {
                "PK": _s(keys.run_pk(status.run_id)),
                "SK": _s(keys.analysis_status_sk(status.analysis_name)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("analysis_status"),
                "analysis_name": _s(status.analysis_name),
                "metric_version": _s(str(status.metric_version)),
                "completed_cycles": _s(_dumps(list(status.completed_cycles))),
                "updated_at": _s(status.updated_at.isoformat()),
            }
        )
        return status

    def get_analysis_status(self, run_id: str, *, analysis_name: str) -> AnalysisStatus | None:
        """How far one analysis has got, or None if it has not started."""
        item = self._get(keys.run_pk(run_id), keys.analysis_status_sk(analysis_name))
        if item is None:
            return None
        return AnalysisStatus(
            run_id=run_id,
            analysis_name=_text(item, "analysis_name"),
            metric_version=_text(item, "metric_version"),
            completed_cycles=tuple(json.loads(_text(item, "completed_cycles"))),
            updated_at=datetime.fromisoformat(_text(item, "updated_at")),
        )

    def mark_cycle_analysed(self, run_id: str, *, cycle: int, detail: Mapping[str, Any]) -> bool:
        """Record that analysis finished for one committed cycle.

        Returns:
            True when this call wrote the marker, False when it was already there.
            The analysis Lambda uses the answer to decide whether a redelivered
            event is work or an echo.
        """
        try:
            self._put(
                {
                    "PK": _s(keys.run_pk(run_id)),
                    "SK": _s(keys.analysis_sk(cycle)),
                    "schema_version": _n(SCHEMA_VERSION),
                    "record_type": _s("analysis_cycle"),
                    "cycle": _n(cycle),
                    "payload": _s(_dumps(dict(detail))),
                    "created_at": _s(self.clock().isoformat()),
                },
                condition="attribute_not_exists(#sk)",
                names={"#sk": "SK"},
            )
        except ClientError as exc:
            if _code(exc) != "ConditionalCheckFailedException":
                raise
            return False
        return True

    def release_cycle_analysis(self, run_id: str, *, cycle: int) -> None:
        """Drop the marker again, so a failed analysis can be retried.

        Claiming before the work is what stops two deliveries of one event from both
        analysing; releasing on failure is what stops a crash from making the claim
        permanent and the cycle silently unanalysed forever.
        """
        self.client.delete_item(
            TableName=self.table_name,
            Key={"PK": _s(keys.run_pk(run_id)), "SK": _s(keys.analysis_sk(cycle))},
        )

    def get_cycle_analysis(self, run_id: str, *, cycle: int) -> dict[str, Any] | None:
        """What analysis recorded for one cycle, or None if it has not run."""
        item = self._get(keys.run_pk(run_id), keys.analysis_sk(cycle))
        if item is None:
            return None
        loaded: dict[str, Any] = json.loads(_text(item, "payload"))
        return loaded

    def store_analysis_artifact(
        self, run_id: str, *, name: str, payload: Mapping[str, Any]
    ) -> None:
        """Persist one derived analysis document under a stable name."""
        self._put(
            {
                "PK": _s(keys.run_pk(run_id)),
                "SK": _s(keys.artifact_sk(name)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("analysis_artifact"),
                "name": _s(name),
                "payload": _b(_pack(dict(payload))),
                "updated_at": _s(self.clock().isoformat()),
            }
        )

    def get_analysis_artifact(self, run_id: str, *, name: str) -> dict[str, Any] | None:
        """The derived document stored under ``name``, or None."""
        item = self._get(keys.run_pk(run_id), keys.artifact_sk(name))
        if item is None:
            return None
        loaded: dict[str, Any] = _unpack(_binary(item, "payload"))
        return loaded

    # ------------------------------------------------------------------ export

    def store_export_manifest(self, manifest: ExportManifestRecord) -> ExportManifestRecord:
        """Record one completed export."""
        self._put(
            {
                "PK": _s(keys.run_pk(manifest.run_id)),
                "SK": _s(keys.export_sk(manifest.export_id)),
                "schema_version": _n(SCHEMA_VERSION),
                "record_type": _s("export_manifest"),
                "export_id": _s(manifest.export_id),
                "run_kind": _s(manifest.run_kind.value),
                "directory": _s(manifest.directory),
                "payload": _s(_dumps(manifest.model_dump(mode="json"))),
                "created_at": _s(manifest.created_at.isoformat()),
            }
        )
        return manifest

    def get_export_manifest(self, run_id: str, *, export_id: str) -> ExportManifestRecord | None:
        """One export's manifest, or None."""
        item = self._get(keys.run_pk(run_id), keys.export_sk(export_id))
        if item is None:
            return None
        return ExportManifestRecord.model_validate(json.loads(_text(item, "payload")))

    def list_export_manifests(self, run_id: str) -> tuple[ExportManifestRecord, ...]:
        """Every export recorded for a run, newest first."""
        manifests = [
            ExportManifestRecord.model_validate(json.loads(_text(item, "payload")))
            for item in self._query(keys.run_pk(run_id), prefix=keys.EXPORT_PREFIX)
        ]
        return tuple(sorted(manifests, key=lambda m: m.created_at, reverse=True))


# ---------------------------------------------------------------------- helpers


def _code(exc: ClientError) -> str:
    code: str = exc.response.get("Error", {}).get("Code", "")
    return code


def _snapshot(item: Mapping[str, Any]) -> ArmCycleSnapshot:
    return ArmCycleSnapshot.model_validate(json.loads(_text(item, "payload")))


def _state(item: Mapping[str, Any]) -> MemoryState:
    return MemoryState.model_validate(json.loads(_text(item, "payload")))


def _run_from_item(item: Mapping[str, Any]) -> RunRecord:
    from attention_sink.pilot.configuration import PilotRunConfiguration, RunKind

    return RunRecord(
        run_id=_text(item, "run_id"),
        run_kind=RunKind(_text(item, "run_kind")),
        status=RunStatus(_text(item, "status")),
        current_cycle=int(item["current_cycle"]["N"]),
        version=int(item["version"]["N"]),
        paused=bool(item["paused"]["BOOL"]),
        configuration=PilotRunConfiguration.model_validate(
            json.loads(_text(item, "configuration"))
        ),
        usage=ModelUsage.model_validate(json.loads(_text(item, "usage"))),
        created_at=datetime.fromisoformat(_text(item, "created_at")),
        updated_at=datetime.fromisoformat(_text(item, "updated_at")),
    )


def merge_usage(previous: ModelUsage, addition: ModelUsage) -> ModelUsage:
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


def table_definition(table_name: str) -> dict[str, Any]:
    """The table this adapter needs, as ``create_table`` arguments.

    Lives beside the adapter that queries it so the CDK stack, the tests, and the
    reader all describe the same two indexes. The CDK is still the deployer; this is
    what the tests create and what the assertions are checked against.
    """
    return {
        "TableName": table_name,
        "BillingMode": "PAY_PER_REQUEST",
        "KeySchema": [
            {"AttributeName": "PK", "KeyType": "HASH"},
            {"AttributeName": "SK", "KeyType": "RANGE"},
        ],
        "AttributeDefinitions": [
            {"AttributeName": "PK", "AttributeType": "S"},
            {"AttributeName": "SK", "AttributeType": "S"},
            {"AttributeName": "GSI1PK", "AttributeType": "S"},
            {"AttributeName": "GSI1SK", "AttributeType": "S"},
        ],
        "GlobalSecondaryIndexes": [
            {
                "IndexName": RUN_LISTING_INDEX,
                "KeySchema": [
                    {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                    {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }
        ],
    }
