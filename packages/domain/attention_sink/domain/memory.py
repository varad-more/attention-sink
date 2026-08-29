"""The unit of episodic memory, and the lineage that survives its compression.

A ``Memory`` is a record, not a mutable object: every transition returns a new,
fully revalidated instance. Nothing in this package edits a memory in place, which
is what lets a cycle be replayed from the ledger and produce the same records.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.enums import (
    RETIRED_STATUSES,
    ArmId,
    LineageRelation,
    MemoryKind,
    MemoryStatus,
)
from attention_sink.domain.hashing import content_hash
from attention_sink.domain.identifiers import CycleNumber, MemoryId, RunId, StimulusId

__all__ = ["MIN_SUMMARY_SOURCES", "Memory", "MemoryLineageEdge", "make_memory_id"]

MIN_SUMMARY_SOURCES = 2
"""A summary of one memory is a rewrite, not a compression, so two is the floor."""


def make_memory_id(arm_id: ArmId, creation_sequence: int) -> MemoryId:
    """Build the deterministic memory identifier for an arm-local creation slot.

    Identifiers are readable rather than opaque because they are the primary handle
    in provenance and citation-audit output. ``creation_sequence`` never repeats
    within an arm, so the identifier is unique within a run.

    Raises:
        ValueError: ``creation_sequence`` is negative.
    """
    if creation_sequence < 0:
        msg = f"creation_sequence must be non-negative, got {creation_sequence}"
        raise ValueError(msg)
    return f"mem_{arm_id.value}_{creation_sequence:06d}"


class MemoryLineageEdge(BaseModel):
    """A directed link from a memory to the memory that descends from it.

    Recorded separately from the child's ``parent_memory_ids`` because lineage has
    to stay queryable in the direction a reader actually asks in -- "what became of
    this memory?" -- long after the parent has left the active set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    parent_memory_id: MemoryId
    child_memory_id: MemoryId
    relation: LineageRelation
    cycle: CycleNumber

    @model_validator(mode="after")
    def _reject_self_reference(self) -> Self:
        if self.parent_memory_id == self.child_memory_id:
            msg = f"memory {self.parent_memory_id} cannot descend from itself"
            raise ValueError(msg)
        return self


class Memory(BaseModel):
    """One episodic memory belonging to exactly one arm of exactly one run.

    Invariants enforced here, so that no other layer has to trust its caller:

    * ``memory_kind == SUMMARY`` if and only if the record names at least
      :data:`MIN_SUMMARY_SOURCES` parents. This is the lineage guarantee.
    * ``status == ACTIVE`` if and only if ``retirement_cycle`` is unset. A retired
      memory can never read as active.
    * A pinned memory is always active. Pinning is what makes retirement illegal, so
      a pinned-and-retired record is a contradiction rather than a state.
    * ``content_hash`` matches ``text``. Filled in automatically when omitted, and
      verified when supplied, so a record whose text was edited after the fact
      fails to load rather than passing as canonical.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: MemoryId
    run_id: RunId
    arm_id: ArmId
    text: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    memory_kind: MemoryKind
    status: MemoryStatus = MemoryStatus.ACTIVE
    birth_cycle: CycleNumber
    retirement_cycle: CycleNumber | None = None
    citation_count: int = Field(default=0, ge=0)
    discounted_citation_score: float = Field(default=0.0, ge=0.0)
    last_verified_citation_cycle: CycleNumber | None = None
    pinned: bool = False
    source_stimulus_id: StimulusId | None = None
    parent_memory_ids: tuple[MemoryId, ...] = ()
    content_hash: str = ""
    creation_sequence: int = Field(ge=0)
    """Arm-local monotonic insertion counter.

    Not in the same role as ``birth_cycle``: several memories may be born in one
    cycle, and FIFO ordering has to be total. This is the field that makes it so,
    and it is stored rather than derived because retiring the newest memory must
    not free its slot for reuse.
    """

    @model_validator(mode="before")
    @classmethod
    def _fill_content_hash(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("content_hash"):
            text = data.get("text")
            if isinstance(text, str) and text:
                return {**data, "content_hash": content_hash(text)}
        return data

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        expected = content_hash(self.text)
        if self.content_hash != expected:
            msg = f"memory {self.memory_id} content hash does not match its text"
            raise ValueError(msg)

        is_summary = self.memory_kind is MemoryKind.SUMMARY
        if is_summary and len(self.parent_memory_ids) < MIN_SUMMARY_SOURCES:
            msg = (
                f"summary {self.memory_id} names {len(self.parent_memory_ids)} sources; "
                f"at least {MIN_SUMMARY_SOURCES} are required"
            )
            raise ValueError(msg)
        if not is_summary and self.parent_memory_ids:
            msg = f"non-summary memory {self.memory_id} must not claim parent memories"
            raise ValueError(msg)
        if len(set(self.parent_memory_ids)) != len(self.parent_memory_ids):
            msg = f"summary {self.memory_id} lists a parent more than once"
            raise ValueError(msg)
        if self.memory_id in self.parent_memory_ids:
            msg = f"summary {self.memory_id} cannot be its own parent"
            raise ValueError(msg)

        retired = self.status in RETIRED_STATUSES
        if retired and self.retirement_cycle is None:
            msg = f"memory {self.memory_id} is {self.status.value} but names no retirement cycle"
            raise ValueError(msg)
        if not retired and self.retirement_cycle is not None:
            msg = f"active memory {self.memory_id} must not name a retirement cycle"
            raise ValueError(msg)
        if self.retirement_cycle is not None and self.retirement_cycle < self.birth_cycle:
            msg = f"memory {self.memory_id} retires before it is born"
            raise ValueError(msg)
        if retired and self.pinned:
            msg = f"pinned memory {self.memory_id} cannot be {self.status.value}"
            raise ValueError(msg)

        if self.last_verified_citation_cycle is not None:
            if self.citation_count == 0:
                msg = f"memory {self.memory_id} names a citation cycle but no citations"
                raise ValueError(msg)
            if self.last_verified_citation_cycle < self.birth_cycle:
                msg = f"memory {self.memory_id} was cited before it was born"
                raise ValueError(msg)
        return self

    @property
    def is_active(self) -> bool:
        """Whether this memory is still in the active set."""
        return self.status is MemoryStatus.ACTIVE

    @property
    def retention_density(self) -> float:
        """Discounted citation score per budget token.

        The quantity the citation-weighted arm minimises. Dividing by cost is what
        stops that arm from being a slow FIFO on long memories: a memory earns its
        space, and a long memory has to earn more of it.
        """
        return self.discounted_citation_score / max(self.token_count, 1)

    def evolve(self, **changes: object) -> Memory:
        """Return a revalidated copy with ``changes`` applied.

        Deliberately not ``model_copy``: that skips validation, which would let a
        transition produce a record no constructor would have accepted.
        """
        data: dict[str, object] = {**self.model_dump(), **changes}
        return Memory.model_validate(data)

    def retire(self, *, status: MemoryStatus, cycle: int) -> Memory:
        """Return a copy that has left the active set.

        Raises:
            ValueError: ``status`` is not a retired status, or the memory is pinned.
        """
        if status not in RETIRED_STATUSES:
            msg = f"{status.value} is not a retirement status"
            raise ValueError(msg)
        return self.evolve(status=status, retirement_cycle=cycle)

    def with_citation_cycle(self, *, cycle: int, citations: int, decay: float) -> Memory:
        """Return a copy with this cycle's verified citations folded in.

        Applies ``new = decay * previous + citations`` to the discounted score, per
        the run's configured decay, and advances the verified-citation cycle only
        when this cycle actually produced citations.
        """
        score = decay * self.discounted_citation_score + citations
        changes: dict[str, object] = {"discounted_citation_score": score}
        if citations:
            changes["citation_count"] = self.citation_count + citations
            changes["last_verified_citation_cycle"] = cycle
        return self.evolve(**changes)
