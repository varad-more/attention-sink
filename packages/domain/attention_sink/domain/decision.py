"""The output of the policy engine: an auditable verdict for one arm-cycle.

A decision is produced by deterministic code before any memory changes, is
persisted verbatim, and is then applied mechanically by
:meth:`~attention_sink.domain.state.MemoryState.apply`. Keeping the decision and its
application separate is what makes forgetting explainable after the fact and
replayable from stored state alone.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.enums import RETIRED_STATUSES, ArmId, MemoryStatus, PolicyDecisionCode
from attention_sink.domain.identifiers import CycleNumber, MemoryId, RunId, Version
from attention_sink.domain.memory import MIN_SUMMARY_SOURCES, Memory, MemoryLineageEdge

__all__ = [
    "CandidateRank",
    "CompressionPlan",
    "MemoryRetirement",
    "PolicyDecision",
    "RandomDraw",
    "RandomProvenance",
]

_HEX_DIGEST = r"^[0-9a-f]{64}$"


class CandidateRank(BaseModel):
    """One eligible memory and the position the policy's ordering gave it.

    The full ordering is recorded, not just the memories that were retired, because
    "this memory survived, and here is exactly how close it came" is the evidence a
    reader needs to believe the ranking was the ranking claimed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: MemoryId
    rank_index: int = Field(ge=0)
    rank_key: str = Field(min_length=1)
    """Canonical rendering of the ordering tuple, ending in the memory identifier.

    Machine-generated from policy state only. It is provenance, not model output.
    """


class RandomDraw(BaseModel):
    """One pseudo-random selection, with everything needed to replay it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    decision_index: int = Field(ge=0)
    digest: str = Field(pattern=_HEX_DIGEST)
    candidate_memory_ids: tuple[MemoryId, ...] = Field(min_length=1)
    selected_index: int = Field(ge=0)
    selected_memory_id: MemoryId

    @model_validator(mode="after")
    def _require_consistent_selection(self) -> Self:
        if len(set(self.candidate_memory_ids)) != len(self.candidate_memory_ids):
            msg = f"draw {self.decision_index} lists a candidate more than once"
            raise ValueError(msg)
        if sorted(self.candidate_memory_ids) != list(self.candidate_memory_ids):
            msg = f"draw {self.decision_index} candidates are not in the digest's sort order"
            raise ValueError(msg)
        if self.selected_index >= len(self.candidate_memory_ids):
            msg = f"draw {self.decision_index} selected index {self.selected_index} out of range"
            raise ValueError(msg)
        if self.candidate_memory_ids[self.selected_index] != self.selected_memory_id:
            msg = (
                f"draw {self.decision_index} names {self.selected_memory_id} but index "
                f"{self.selected_index} holds {self.candidate_memory_ids[self.selected_index]}"
            )
            raise ValueError(msg)
        return self


class RandomProvenance(BaseModel):
    """The complete derivation of a stochastic arm's choices for one cycle.

    Sufficient on its own: given the run seed recorded here and these draws, the
    selection can be recomputed without the original process, which is what makes
    the random arm a control rather than an anecdote.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    algorithm: Literal["sha256-mt19937-v1"] = "sha256-mt19937-v1"
    run_random_seed: str = Field(min_length=1)
    draws: tuple[RandomDraw, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _require_ordered_draws(self) -> Self:
        indices = [draw.decision_index for draw in self.draws]
        if indices != list(range(len(indices))):
            msg = f"random draws must be indexed 0..n-1 in order, got {indices}"
            raise ValueError(msg)
        return self


class CompressionPlan(BaseModel):
    """An instruction to replace source memories with one lossy summary.

    The policy chooses *what* is compressed and *how large* the result may be; a
    model later chooses the words. Splitting it this way keeps the model out of the
    eviction decision while still allowing real summarisation, and keeps the
    summary's tokens charged against the same budget as any other memory.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_memory_ids: tuple[MemoryId, ...] = Field(min_length=MIN_SUMMARY_SOURCES)
    summary_memory_id: MemoryId
    summary_target_token_limit: int = Field(gt=0)
    tokens_freed: int = Field(ge=0)
    """Budget tokens the sources currently occupy, before the summary is charged."""

    safety_margin_tokens: int = Field(ge=0)
    """Headroom the plan leaves below the budget, from the run's configuration."""

    @model_validator(mode="after")
    def _require_distinct_sources(self) -> Self:
        if len(set(self.source_memory_ids)) != len(self.source_memory_ids):
            msg = "compression sources must be distinct"
            raise ValueError(msg)
        if self.summary_memory_id in self.source_memory_ids:
            msg = f"summary {self.summary_memory_id} cannot compress itself"
            raise ValueError(msg)
        return self


class MemoryRetirement(BaseModel):
    """One memory leaving the active set, and the verdict that removed it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: MemoryId
    status: MemoryStatus
    reason: PolicyDecisionCode

    @model_validator(mode="after")
    def _require_retired_status(self) -> Self:
        if self.status not in RETIRED_STATUSES:
            msg = f"{self.status.value} is not a retirement status"
            raise ValueError(msg)
        return self


class PolicyDecision(BaseModel):
    """The complete, replayable verdict for one arm in one cycle.

    A decision is *final* when it asks for nothing further -- that is, when it names
    no outstanding :attr:`compression_plan`. Only final decisions are required to be
    within budget, because the summarising arm legitimately passes through an
    intermediate state where it has decided what to compress but the summary does
    not exist yet.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    policy_version: Version
    decision_code: PolicyDecisionCode

    budget_tokens: int = Field(gt=0)
    tokens_before: int = Field(ge=0)
    tokens_after: int = Field(ge=0)

    kept_memory_ids: tuple[MemoryId, ...] = ()
    retired_memory_ids: tuple[MemoryId, ...] = ()
    retirements: tuple[MemoryRetirement, ...] = ()
    created_memories: tuple[Memory, ...] = ()
    lineage_edges: tuple[MemoryLineageEdge, ...] = ()
    candidate_order: tuple[CandidateRank, ...] = ()

    random_provenance: RandomProvenance | None = None
    compression_plan: CompressionPlan | None = None
    committed_compression: CompressionPlan | None = None
    """The plan this decision has just carried out, distinct from one it requests.

    A finalisation that still leaves the arm over budget both commits a summary and
    asks for another, so conflating the two fields would make the second round
    ambiguous about which compression it was describing.
    """

    explanation: str = Field(min_length=1)
    """Deterministic prose from :mod:`attention_sink.domain.explain`. Never generated."""

    @property
    def is_final(self) -> bool:
        """Whether this decision completes the cycle rather than requesting a summary."""
        return self.compression_plan is None

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        retired_from_detail = tuple(r.memory_id for r in self.retirements)
        if self.retired_memory_ids != retired_from_detail:
            msg = "retired_memory_ids does not match the recorded retirements"
            raise ValueError(msg)
        for name, ids in (("kept", self.kept_memory_ids), ("retired", self.retired_memory_ids)):
            if len(set(ids)) != len(ids):
                msg = f"{name} memory ids contain a duplicate"
                raise ValueError(msg)
        overlap = set(self.kept_memory_ids) & set(self.retired_memory_ids)
        if overlap:
            msg = f"memories both kept and retired: {sorted(overlap)}"
            raise ValueError(msg)

        ranked = [c.memory_id for c in self.candidate_order]
        if [c.rank_index for c in self.candidate_order] != list(range(len(ranked))):
            msg = "candidate_order rank indices must run 0..n-1 in order"
            raise ValueError(msg)
        if len(set(ranked)) != len(ranked):
            msg = "candidate_order lists a memory more than once"
            raise ValueError(msg)
        unranked = set(self.retired_memory_ids) - set(ranked)
        if unranked:
            msg = f"retired memories absent from the recorded ordering: {sorted(unranked)}"
            raise ValueError(msg)

        created = {memory.memory_id for memory in self.created_memories}
        if not created <= set(self.kept_memory_ids):
            msg = f"created memories are not kept: {sorted(created - set(self.kept_memory_ids))}"
            raise ValueError(msg)
        edge_children = {edge.child_memory_id for edge in self.lineage_edges}
        if not edge_children <= created:
            orphans = sorted(edge_children - created)
            msg = f"lineage edges name children this decision did not create: {orphans}"
            raise ValueError(msg)

        if self.random_provenance is not None and len(self.random_provenance.draws) != len(
            self.retirements
        ):
            msg = "random provenance must record exactly one draw per retirement"
            raise ValueError(msg)

        if self.committed_compression is not None:
            compressed = {
                r.memory_id for r in self.retirements if r.status is MemoryStatus.COMPRESSED
            }
            if compressed != set(self.committed_compression.source_memory_ids):
                msg = "committed compression sources do not match the memories marked compressed"
                raise ValueError(msg)

        if self.is_final and self.tokens_after > self.budget_tokens:
            msg = (
                f"final decision leaves {self.tokens_after} tokens active, over the "
                f"{self.budget_tokens}-token budget"
            )
            raise ValueError(msg)
        return self
