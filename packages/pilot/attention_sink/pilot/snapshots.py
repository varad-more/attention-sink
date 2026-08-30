"""The immutable records one pilot cycle leaves behind.

Pilot V1 stores snapshots rather than an event stream (ADR-008-pilot). A snapshot is
the complete state of one arm at the end of one cycle, plus everything that produced
it: what it held going in, what it wrote, what it claimed, what the mechanism decided,
and what it holds coming out. Nothing downstream has to fold a stream of events to
learn what an arm remembered on cycle 17.

Every snapshot carries its own digest, taken over the canonical serialisation of every
other field. Two processes that ran the same cycle produce the same hash, so a replay
that diverges is visible at the cycle it diverged rather than in a metric several
steps later.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain import (
    ArmId,
    CitationClaim,
    CycleNumber,
    Memory,
    MemoryId,
    MemoryState,
    MemoryStatus,
    PolicyDecision,
    PolicyDecisionCode,
    RunId,
    StimulusId,
    UtcTimestamp,
    VerifiedCitation,
    Version,
)
from attention_sink.model_gateway import CallMetadata
from attention_sink.pilot.budget import ModelUsage
from attention_sink.pilot.canonical import canonical_digest
from attention_sink.pilot.configuration import PilotRunConfiguration

__all__ = [
    "CLAIMED_VALIDATOR_VERSION",
    "ArmCycleSnapshot",
    "MemoryStatistic",
    "RejectedClaim",
    "RetiredMemoryRecord",
    "RunSnapshot",
    "RunStatus",
    "StimulusRecord",
]

CLAIMED_VALIDATOR_VERSION = "claimed.validated-v1"
"""Recorded as the ``auditor_version`` of a citation accepted without an audit.

A ``VerifiedCitation`` names what concluded it. In the pilot that is structural
validation, not a model, and saying so in the record is what stops a later reader
from mistaking these for audited citations. See docs/pilot-scope.md.
"""


class RunStatus(StrEnum):
    """Where a run has got to."""

    INITIALIZED = "initialized"
    """Seeds installed, no cycle run. The state every checkpoint at cycle 0 sees."""

    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    """A cycle could not be staged. The run stopped at the last committed cycle."""


class StimulusRecord(BaseModel):
    """The one event every arm received this cycle, stored verbatim.

    Only ``text`` was ever shown to a model. The phase and reliability are recorded
    here because a reader of the snapshot needs them and a writer must not have them.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    stimulus_id: StimulusId
    cycle: CycleNumber
    phase: str = Field(min_length=1)
    reliability: str = Field(min_length=1)
    text: str = Field(min_length=1)


class MemoryStatistic(BaseModel):
    """One active memory's policy-visible state at a point in the cycle.

    A projection of :class:`~attention_sink.domain.Memory` rather than the record
    itself: these are the fields a mechanism reads, and storing them separately is
    what lets a reader see *why* an arm retired what it did without reconstructing
    the whole state.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: MemoryId
    memory_kind: str
    birth_cycle: CycleNumber
    token_count: int = Field(ge=1)
    citation_count: int = Field(ge=0)
    discounted_citation_score: float = Field(ge=0.0)
    last_verified_citation_cycle: CycleNumber | None = None
    pinned: bool = False

    @classmethod
    def of(cls, memory: Memory) -> MemoryStatistic:
        """Project one memory onto the fields a mechanism can see."""
        return cls(
            memory_id=memory.memory_id,
            memory_kind=memory.memory_kind.value,
            birth_cycle=memory.birth_cycle,
            token_count=memory.token_count,
            citation_count=memory.citation_count,
            discounted_citation_score=memory.discounted_citation_score,
            last_verified_citation_cycle=memory.last_verified_citation_cycle,
            pinned=memory.pinned,
        )


class RejectedClaim(BaseModel):
    """A citation the writer made that validation did not sustain.

    Kept rather than discarded. How often an arm cites something it was not given, or
    cites the same memory twice, is a finding about that arm.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: str = Field(min_length=1, max_length=256)
    """Deliberately a plain string, not a ``MemoryId``.

    This field records what was *claimed*, and a claim can be malformed. Constraining
    it would turn "that identifier does not exist" -- the first thing validation is
    supposed to catch -- into an exception raised while recording the catch."""

    reason: Literal["duplicate", "not_active"]


class RetiredMemoryRecord(BaseModel):
    """One memory that left the active set this cycle, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: MemoryId
    status: MemoryStatus
    reason: PolicyDecisionCode
    token_count: int = Field(ge=1)
    text: str = Field(min_length=1)
    """Stored with the retirement. What an arm has lost is the subject of the
    experiment, and a reader must not have to reconstruct it from an earlier cycle."""


class ArmCycleSnapshot(BaseModel):
    """The complete, immutable record of one arm in one cycle.

    Named for the arm as well as the cycle because the pilot's unit of record is the
    arm-cycle: six of these are staged together and either all six are committed or
    none is.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    stimulus: StimulusRecord

    active_memory_ids_before: tuple[MemoryId, ...]
    memory_statistics_before_rebalance: tuple[MemoryStatistic, ...]
    tokens_before: int = Field(ge=0)

    journal_entry: str = Field(min_length=1)
    candidate_memory: str = Field(min_length=1)
    candidate_memory_id: MemoryId
    claimed_citations: tuple[CitationClaim, ...] = ()
    validated_citations: tuple[VerifiedCitation, ...] = ()
    rejected_claims: tuple[RejectedClaim, ...] = ()

    policy_decisions: tuple[PolicyDecision, ...] = Field(min_length=1)
    """Every decision this cycle produced, in order.

    Usually one. The summarising arm produces two or three: a plan, then the
    commitment of the summary written for it, and then whatever the arm still needed
    after that. Storing the sequence rather than only the last is what makes a
    two-stage compression auditable."""

    created_summary: Memory | None = None
    """The summary this cycle admitted, when it admitted one.

    The last, in the rare cycle that needed two compressions to reach the budget.
    Every summary created is carried in full inside `policy_decisions`; this field
    is the projection a reader almost always wants."""

    summary_source_memory_ids: tuple[MemoryId, ...] = ()
    retired_memories: tuple[RetiredMemoryRecord, ...] = ()
    compressed_memory_ids: tuple[MemoryId, ...] = ()

    active_memory_ids_after: tuple[MemoryId, ...]
    tokens_after: int = Field(ge=0)
    budget_tokens: int = Field(gt=0)
    state_hash: str = Field(min_length=1)

    model_metadata: tuple[CallMetadata, ...] = ()
    policy_version: Version
    prompt_hashes: dict[str, str] = Field(default_factory=dict)
    simulated: bool
    completed_at: UtcTimestamp
    snapshot_hash: str = ""

    @model_validator(mode="after")
    def _check_snapshot(self) -> Self:
        if self.tokens_after > self.budget_tokens:
            msg = (
                f"{self.arm_id.value} cycle {self.cycle} ends holding {self.tokens_after} "
                f"tokens, over the {self.budget_tokens}-token budget"
            )
            raise ValueError(msg)
        final = self.policy_decisions[-1]
        if not final.is_final:
            msg = (
                f"{self.arm_id.value} cycle {self.cycle} ends on a decision that still "
                f"awaits a summary"
            )
            raise ValueError(msg)
        if final.kept_memory_ids != self.active_memory_ids_after:
            msg = f"{self.arm_id.value} cycle {self.cycle} kept a different set than it recorded"
            raise ValueError(msg)
        if (self.created_summary is None) != (not self.summary_source_memory_ids):
            msg = f"{self.arm_id.value} cycle {self.cycle} names summary sources without a summary"
            raise ValueError(msg)
        if self.created_summary is not None:
            sources = set(self.created_summary.parent_memory_ids)
            if sources != set(self.summary_source_memory_ids):
                msg = (
                    f"summary {self.created_summary.memory_id} names parents "
                    f"{sorted(sources)} but the snapshot records "
                    f"{sorted(self.summary_source_memory_ids)}"
                )
                raise ValueError(msg)
            # Subset rather than equality: a cycle that needed two compressions
            # compressed the sources of both, and this field names the last summary.
            # The full sequence is in `policy_decisions`.
            if not sources <= set(self.compressed_memory_ids):
                msg = (
                    f"summary {self.created_summary.memory_id} descends from "
                    f"{sorted(sources)} but {sorted(self.compressed_memory_ids)} were compressed"
                )
                raise ValueError(msg)
        return self

    @property
    def unhashed_payload(self) -> dict[str, object]:
        """This snapshot's content, excluding the digest taken over it."""
        return {k: v for k, v in self.model_dump(mode="json").items() if k != "snapshot_hash"}

    def sealed(self) -> ArmCycleSnapshot:
        """Return a copy carrying the digest of its own content.

        Taken after every other field is set, over the canonical serialisation, so
        two processes that ran the same cycle produce the same hash.
        """
        return self.model_copy(update={"snapshot_hash": canonical_digest(self.unhashed_payload)})

    def verify_hash(self) -> bool:
        """Whether the recorded digest still matches the content it covers."""
        return bool(self.snapshot_hash) and self.snapshot_hash == canonical_digest(
            self.unhashed_payload
        )


class RunSnapshot(BaseModel):
    """The state of the whole run: every arm, the cycle reached, and what was spent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    configuration: PilotRunConfiguration
    status: RunStatus
    current_cycle: CycleNumber
    arm_states: dict[str, MemoryState]
    """Every arm's complete memory, keyed by arm identifier."""

    usage: ModelUsage
    updated_at: UtcTimestamp
    snapshot_hash: str = ""

    @model_validator(mode="after")
    def _check_arms(self) -> Self:
        configured = {arm.value for arm in self.configuration.arms}
        held = set(self.arm_states)
        if configured != held:
            msg = (
                f"run snapshot holds states for {sorted(held)} but the run configures "
                f"{sorted(configured)}"
            )
            raise ValueError(msg)
        if self.current_cycle > self.configuration.max_cycles:
            msg = (
                f"run is at cycle {self.current_cycle}, past the configured "
                f"{self.configuration.max_cycles}"
            )
            raise ValueError(msg)
        return self

    @property
    def unhashed_payload(self) -> dict[str, object]:
        """This snapshot's content, excluding the digest taken over it."""
        return {k: v for k, v in self.model_dump(mode="json").items() if k != "snapshot_hash"}

    def sealed(self) -> RunSnapshot:
        """Return a copy carrying the digest of its own content."""
        return self.model_copy(update={"snapshot_hash": canonical_digest(self.unhashed_payload)})

    def verify_hash(self) -> bool:
        """Whether the recorded digest still matches the content it covers."""
        return bool(self.snapshot_hash) and self.snapshot_hash == canonical_digest(
            self.unhashed_payload
        )
