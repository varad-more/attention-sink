"""What the pilot needs from a store, stated without naming one.

These are ports, and they live with the application rather than with the adapter that
satisfies them. The SQLite adapter in Phases 5-6 and the DynamoDB adapter in Phase 7
both implement this protocol; nothing above the adapter line imports either. A method
that appeared here because SQLite made it easy would be the first crack in that, so
every method below exists because a cycle, a checkpoint, an analysis, or an export
needs it.

The records are Pydantic models rather than rows: an adapter serialises them however
it likes, and the shape a service reasons about does not change when the store does.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain import (
    ArmId,
    CycleNumber,
    MemoryId,
    MemoryState,
    MetricEvidence,
    RunId,
    UtcTimestamp,
    Version,
)
from attention_sink.pilot.budget import ModelUsage
from attention_sink.pilot.canonical import canonical_digest
from attention_sink.pilot.configuration import PilotRunConfiguration, RunKind
from attention_sink.pilot.snapshots import ArmCycleSnapshot, RunStatus

__all__ = [
    "AnalysisStatus",
    "ConcurrentRunUpdate",
    "CycleLock",
    "ExportManifestRecord",
    "LockNotHeld",
    "PersistenceError",
    "PilotRepository",
    "PreparedCycle",
    "PreparedCycleConflict",
    "RunRecord",
    "StoredInterview",
]


class PersistenceError(RuntimeError):
    """A store refused an operation. Never raised for a store being unreachable."""


class ConcurrentRunUpdate(PersistenceError):
    """The run moved underneath this caller. Nothing was written."""


class LockNotHeld(PersistenceError):
    """The cycle lock this caller presented is not the one the run is holding."""


class PreparedCycleConflict(PersistenceError):
    """A prepared cycle already exists for this cycle with different content.

    Not a retry: a retry presents identical content and is reused. Different content
    for the same cycle means two processes staged different experiments and one of
    them must lose, loudly.
    """


# ------------------------------------------------------------------- records


class RunRecord(BaseModel):
    """One run's mutable head: where it is, what it is, and what it has spent.

    ``version`` is an optimistic-concurrency counter, not a schema version. Every
    write that advances a run supplies the version it read, and a mismatch means
    somebody else advanced it first.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    run_kind: RunKind
    status: RunStatus
    current_cycle: CycleNumber
    version: int = Field(ge=0)
    configuration: PilotRunConfiguration
    usage: ModelUsage = ModelUsage()
    paused: bool = False
    """Set by an operator. The scheduler refuses to advance a paused run."""

    created_at: UtcTimestamp
    updated_at: UtcTimestamp

    @property
    def is_complete(self) -> bool:
        """Whether every configured cycle has been committed."""
        return self.current_cycle >= self.configuration.maximum_cycles

    @property
    def next_cycle(self) -> int:
        """The only cycle this run will accept next."""
        return self.current_cycle + 1


class CycleLock(BaseModel):
    """A claim on the right to advance one run to one cycle.

    Expiry rather than a heartbeat: a local scheduler that dies mid-cycle must not
    wedge the run forever, and a lease that has run out is safe to take precisely
    because a cycle commit re-checks the token inside the same transaction that
    writes.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    cycle: CycleNumber
    token: str = Field(min_length=8, max_length=128)
    invocation_id: str = Field(min_length=1, max_length=128)
    acquired_at: UtcTimestamp
    expires_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_forward_expiry(self) -> Self:
        if self.expires_at <= self.acquired_at:
            msg = f"lock {self.token} expires at or before it was acquired"
            raise ValueError(msg)
        return self

    def is_expired(self, now: datetime) -> bool:
        """Whether this lease has run out and may be replaced."""
        return now >= self.expires_at


class PreparedCycle(BaseModel):
    """Six staged arm results, hashed, held before anything is committed.

    The unit of idempotency. A cycle is generated once, written here, and only then
    committed; a retry that presents the same content reuses this record instead of
    calling six writers again. Never exposed through the read API -- it describes a
    cycle that has not happened yet, and a reader who could see one could see the
    future of the experiment.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    cycle: CycleNumber
    invocation_id: str = Field(min_length=1, max_length=128)
    snapshots: tuple[ArmCycleSnapshot, ...] = Field(min_length=1)
    arm_states: dict[str, MemoryState]
    usage: ModelUsage
    committed: bool = False
    content_hash: str = ""
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_one_cycle_and_matching_arms(self) -> Self:
        cycles = {snapshot.cycle for snapshot in self.snapshots}
        if cycles != {self.cycle}:
            msg = f"prepared cycle {self.cycle} holds snapshots for {sorted(cycles)}"
            raise ValueError(msg)
        arms = {snapshot.arm_id.value for snapshot in self.snapshots}
        if arms != set(self.arm_states):
            msg = (
                f"prepared cycle {self.cycle} snapshots {sorted(arms)} but carries "
                f"states for {sorted(self.arm_states)}"
            )
            raise ValueError(msg)
        return self

    @property
    def unhashed_payload(self) -> dict[str, object]:
        """The content the digest covers: the staged result, not its bookkeeping.

        ``committed`` and ``content_hash`` are excluded. Committing a prepared cycle
        must not change the hash a retry compares against, or every retry after a
        partial failure would look like a conflict.
        """
        excluded = {"content_hash", "committed", "created_at", "invocation_id"}
        return {k: v for k, v in self.model_dump(mode="json").items() if k not in excluded}

    def sealed(self) -> PreparedCycle:
        """Return a copy carrying the digest of its own staged content."""
        return self.model_copy(update={"content_hash": canonical_digest(self.unhashed_payload)})

    def matches(self, other: PreparedCycle) -> bool:
        """Whether two prepared cycles stage the same experiment."""
        return canonical_digest(self.unhashed_payload) == canonical_digest(other.unhashed_payload)


class StoredInterview(BaseModel):
    """One arm's checkpoint interview, as it is kept.

    Carries the state it was taken against as well as the answers. An interview is a
    measurement of a particular memory at a particular moment, and one stored without
    that is a quotation without a source.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    interview_version: Version
    question_set_version: Version
    answers: tuple[dict[str, object], ...]
    """One entry per question: identifier, answer text, cited labels, uncertainty."""

    reported_memory_ids: tuple[MemoryId, ...] = ()
    stated_uncertainty: tuple[str, ...] = ()
    model_metadata: dict[str, object] = Field(default_factory=dict)
    prompt_hash: str = Field(min_length=1)
    input_state_hash: str = Field(min_length=1)
    """Digest of the arm state the interview was taken against."""

    record_hash: str = ""
    completed_at: UtcTimestamp

    @property
    def unhashed_payload(self) -> dict[str, object]:
        """This record's content, excluding the digest taken over it."""
        return {k: v for k, v in self.model_dump(mode="json").items() if k != "record_hash"}

    def sealed(self) -> StoredInterview:
        """Return a copy carrying the digest of its own content."""
        return self.model_copy(update={"record_hash": canonical_digest(self.unhashed_payload)})


class AnalysisStatus(BaseModel):
    """How far analysis has got for one run and one analysis kind.

    Stored so that analysis is resumable and so that a partially analysed run is
    visibly partial rather than quietly short of metrics.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    analysis_name: str = Field(min_length=1, max_length=64)
    metric_version: Version
    completed_cycles: tuple[int, ...] = ()
    updated_at: UtcTimestamp


class ExportManifestRecord(BaseModel):
    """What one export wrote, where, and what it may be called.

    The three labels are not decoration. An export directory is the artefact most
    likely to be read by someone who was not here when it was produced.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    export_id: str = Field(min_length=1, max_length=128)
    run_kind: RunKind
    directory: str = Field(min_length=1)
    files: dict[str, str]
    """File name to SHA-256 digest, for every file written."""

    labels: tuple[str, ...] = ("LOCAL_FIXTURE", "NON_CANONICAL", "SIMULATED_MODEL_OUTPUTS")
    created_at: UtcTimestamp


# ------------------------------------------------------------------ the port


@runtime_checkable
class PilotRepository(Protocol):
    """Everything the pilot stores, and nothing about how.

    One protocol rather than eight, because every method is needed by the same two
    callers -- the cycle service and the analysis service -- and splitting them would
    only mean a caller holding four objects that are always the same object.
    """

    # ------------------------------------------------------------------- runs

    def create_run(self, record: RunRecord) -> RunRecord:
        """Insert a new run.

        Raises:
            PersistenceError: A run with that identifier already exists.
        """
        ...

    def get_run(self, run_id: str) -> RunRecord | None:
        """Return one run's head, or None when it does not exist."""
        ...

    def list_runs(self) -> tuple[RunRecord, ...]:
        """Every run, newest first."""
        ...

    def update_run_status(self, run_id: str, *, status: RunStatus, version: int) -> RunRecord:
        """Move a run to ``status``, if it is still at ``version``.

        Raises:
            ConcurrentRunUpdate: The run has moved on.
        """
        ...

    def add_usage(self, run_id: str, *, usage: ModelUsage) -> RunRecord:
        """Fold model spend into a run's cumulative counters.

        Separate from ``commit_cycle`` because a checkpoint interview happens after
        the cycle it follows has been committed. Without this the interviewer calls
        would never appear in the run's totals, and "cumulative model calls" would be
        a number that quietly excluded a fifth of the spend.

        Raises:
            PersistenceError: No such run.
        """
        ...

    # ------------------------------------------------------------------ locks

    def acquire_cycle_lock(
        self, run_id: str, *, cycle: int, invocation_id: str, ttl_seconds: int
    ) -> CycleLock:
        """Take the right to advance ``run_id`` to ``cycle``.

        An unexpired lock held by someone else is refused. An expired one is replaced.

        Raises:
            LockNotHeld: Another invocation holds an unexpired lock.
        """
        ...

    def release_cycle_lock(self, run_id: str, *, token: str) -> None:
        """Release the lock, if this caller still holds it. Silent when it does not."""
        ...

    def get_cycle_lock(self, run_id: str) -> CycleLock | None:
        """The lock currently recorded for a run, expired or not."""
        ...

    # -------------------------------------------------------- prepared cycles

    def store_prepared_cycle(self, prepared: PreparedCycle) -> PreparedCycle:
        """Persist a staged cycle, or return the identical one already stored.

        Raises:
            PreparedCycleConflict: A different cycle is already prepared for this
                run and cycle number.
        """
        ...

    def get_prepared_cycle(self, run_id: str, *, cycle: int) -> PreparedCycle | None:
        """The staged cycle for ``cycle``, committed or not."""
        ...

    def commit_cycle(
        self, run_id: str, *, cycle: int, token: str, content_hash: str, version: int
    ) -> RunRecord:
        """Commit a prepared cycle: six snapshots, six states, one advance.

        All of it or none of it. See the adapter for what "none of it" costs.

        Raises:
            ConcurrentRunUpdate: The run moved, or is not at ``cycle`` minus one.
            LockNotHeld: ``token`` is not the lock this run is holding.
            PersistenceError: No prepared cycle matches ``content_hash``.
        """
        ...

    # -------------------------------------------------------------- arm state

    def seed_arm_state(self, run_id: str, *, arm_id: ArmId, state: MemoryState) -> None:
        """Install one arm's starting state, before any cycle exists.

        Separate from the commit path on purpose. Cycle 0 is the starting condition,
        not a cycle, and routing it through ``commit_cycle`` would make "cycle 0 was
        committed" a state every invariant afterwards has to special-case.

        Raises:
            PersistenceError: The arm already has a state; seeds are installed once.
        """
        ...

    def get_current_arm_state(self, run_id: str, *, arm_id: ArmId) -> MemoryState | None:
        """One arm's current memory, as of the last committed cycle."""
        ...

    def get_all_current_arm_states(self, run_id: str) -> dict[str, MemoryState]:
        """Every arm's current memory, keyed by arm identifier."""
        ...

    # -------------------------------------------------------------- snapshots

    def get_cycle_snapshot(
        self, run_id: str, *, arm_id: ArmId, cycle: int
    ) -> ArmCycleSnapshot | None:
        """One committed arm-cycle record."""
        ...

    def list_cycle_snapshots(self, run_id: str, *, cycle: int) -> tuple[ArmCycleSnapshot, ...]:
        """Every arm's record for one committed cycle, in configured arm order."""
        ...

    def list_arm_snapshots(self, run_id: str, *, arm_id: ArmId) -> tuple[ArmCycleSnapshot, ...]:
        """One arm's records for every committed cycle, in cycle order."""
        ...

    def list_completed_cycles(self, run_id: str) -> tuple[int, ...]:
        """Every cycle number that has been committed, ascending."""
        ...

    # ------------------------------------------------------------- interviews

    def store_interview(self, interview: StoredInterview) -> StoredInterview:
        """Persist one checkpoint interview. Re-storing an identical one is a no-op.

        Raises:
            PersistenceError: A different interview is already stored for this arm
                and cycle. An interview is a measurement and is not revised.
        """
        ...

    def get_interviews(
        self, run_id: str, *, cycle: int | None = None, arm_id: ArmId | None = None
    ) -> tuple[StoredInterview, ...]:
        """Stored interviews, narrowed by cycle and arm when given."""
        ...

    # ---------------------------------------------------------------- metrics

    def store_metric(self, metric: MetricEvidence) -> MetricEvidence:
        """Persist one scored metric with its evidence."""
        ...

    def get_metrics(
        self,
        run_id: str,
        *,
        metric_name: str | None = None,
        arm_id: ArmId | None = None,
        cycle: int | None = None,
    ) -> tuple[MetricEvidence, ...]:
        """Stored metrics, narrowed by name, arm, and cycle when given."""
        ...

    # ------------------------------------------------------------- embeddings

    def store_embedding(self, run_id: str, *, key: str, record: Mapping[str, object]) -> None:
        """Persist one embedding under a caller-chosen key.

        Keyed rather than content-addressed at this layer, because what an analysis
        wants back is "the identity embedding of arm_fifo at cycle 12", not "the
        vector for this hash".
        """
        ...

    def get_embedding(self, run_id: str, *, key: str) -> dict[str, object] | None:
        """The embedding stored under ``key``, or None."""
        ...

    # ----------------------------------------------------------- token counts

    def store_token_count(self, *, counter_version: str, text_hash: str, tokens: int) -> None:
        """Cache one exact token count, so a re-run does not re-count."""
        ...

    def get_token_count(self, *, counter_version: str, text_hash: str) -> int | None:
        """The cached count for this counter and text, or None."""
        ...

    # --------------------------------------------------------------- analysis

    def store_analysis_status(self, status: AnalysisStatus) -> AnalysisStatus:
        """Record how far one analysis has got."""
        ...

    def get_analysis_status(self, run_id: str, *, analysis_name: str) -> AnalysisStatus | None:
        """How far one analysis has got, or None if it has not started."""
        ...

    # ----------------------------------------------------------------- export

    def store_export_manifest(self, manifest: ExportManifestRecord) -> ExportManifestRecord:
        """Record one completed export."""
        ...

    def get_export_manifest(self, run_id: str, *, export_id: str) -> ExportManifestRecord | None:
        """One export's manifest, or None."""
        ...

    def list_export_manifests(self, run_id: str) -> tuple[ExportManifestRecord, ...]:
        """Every export recorded for a run, newest first."""
        ...


def arm_order(configuration: PilotRunConfiguration) -> Sequence[ArmId]:
    """The order arms are stored and returned in, for every store.

    Configured order, never completion order or alphabetical order. Two runs of the
    same protocol must produce byte-identical exports, and an ordering that depended
    on which arm finished first would not.
    """
    return configuration.arms
