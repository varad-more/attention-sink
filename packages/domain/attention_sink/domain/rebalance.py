"""The output of the policy engine: an auditable plan for one arm-cycle.

A plan is produced by deterministic code before any memory changes, is persisted
verbatim, and is then applied mechanically. Keeping the decision and its
application separate is what makes eviction causally explainable after the fact and
replayable from stored state.
"""

from __future__ import annotations

import hashlib
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId, DecisionCode
from attention_sink.domain.errors import LineageError, PolicyError
from attention_sink.domain.memory import MemoryRecord

__all__ = [
    "CompressionRequest",
    "RebalanceContext",
    "RebalanceDecision",
    "RebalancePlan",
    "apply_plan",
    "derive_arm_cycle_seed",
]

_EVICTION_CODES = frozenset(
    {
        DecisionCode.EVICTED_OLDEST,
        DecisionCode.EVICTED_LEAST_RECENTLY_CITED,
        DecisionCode.EVICTED_LOWEST_CITATION_WEIGHT,
        DecisionCode.EVICTED_OUTSIDE_WINDOW,
        DecisionCode.EVICTED_RANDOM,
        DecisionCode.EVICTED_STATELESS,
        DecisionCode.COMPRESSED,
    }
)


def derive_arm_cycle_seed(run_seed: str, arm_id: ArmId, cycle_index: int) -> int:
    """Derive the pseudo-random seed for one arm-cycle from the recorded run seed.

    Derivation is a pure function of logged values, so a stochastic policy's choice
    is reproducible from the run manifest alone. BLAKE2b is used as a stable
    cross-version digest; Python's ``hash`` is salted per process and must not be
    used anywhere a decision has to be replayed.
    """
    payload = f"{run_seed}|{arm_id.value}|{cycle_index}".encode()
    return int.from_bytes(hashlib.blake2b(payload, digest_size=8).digest(), "big")


class RebalanceContext(BaseModel):
    """Everything a policy is permitted to know when planning a rebalance.

    Deliberately excludes the arm's public name, the state of other arms, any
    prediction, and any metric: a policy decides from mechanism alone.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str = Field(min_length=1)
    arm_id: ArmId
    cycle_index: int = Field(ge=0)
    run_seed: str = Field(min_length=1)
    protocol_version: str = Field(min_length=1)


class CompressionRequest(BaseModel):
    """A policy's instruction to replace source memories with one lossy summary.

    The policy chooses *what* is compressed and *how large* the result may be; a
    model later chooses the words. Splitting it this way keeps the model out of the
    eviction decision while still allowing genuine summarisation, and keeps the
    summary's tokens charged against the same budget as any other memory.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    source_memory_ids: tuple[str, ...] = Field(min_length=1)
    max_summary_tokens: int = Field(gt=0)
    target_origin_ordinal: int = Field(ge=0)

    @model_validator(mode="after")
    def _require_distinct_sources(self) -> Self:
        if len(set(self.source_memory_ids)) != len(self.source_memory_ids):
            msg = "compression sources must be distinct"
            raise ValueError(msg)
        return self


class RebalanceDecision(BaseModel):
    """The verdict for a single memory, with the evidence that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: str = Field(min_length=1)
    code: DecisionCode
    rank_key: str = Field(min_length=1)
    """Human-readable rendering of the ordering key that selected this memory.

    Machine-generated from policy state only. It is provenance, not model output.
    """


class RebalancePlan(BaseModel):
    """The complete, replayable decision for one arm in one cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    cycle_index: int = Field(ge=0)
    policy_version: str = Field(min_length=1)
    retained_memory_ids: tuple[str, ...] = ()
    decisions: tuple[RebalanceDecision, ...] = ()
    compression: CompressionRequest | None = None
    seed_used: int | None = None
    projected_tokens: int = Field(ge=0)
    """Budget tokens the active set will hold once the plan is applied.

    For a compressing plan this charges ``max_summary_tokens`` -- the worst case the
    summary is allowed to cost -- so the projection is an upper bound that the
    commit step re-validates against the summary actually produced.
    """

    @model_validator(mode="after")
    def _require_consistent_decisions(self) -> Self:
        evicted = [d.memory_id for d in self.decisions if d.code in _EVICTION_CODES]
        if len(set(evicted)) != len(evicted):
            msg = "a memory is evicted more than once in one plan"
            raise ValueError(msg)
        overlap = set(evicted) & set(self.retained_memory_ids)
        if overlap:
            msg = f"memories both retained and evicted: {sorted(overlap)}"
            raise ValueError(msg)
        compressed = {d.memory_id for d in self.decisions if d.code is DecisionCode.COMPRESSED}
        sources = set(self.compression.source_memory_ids) if self.compression else set()
        if compressed != sources:
            msg = "compression sources do not match the memories marked compressed"
            raise ValueError(msg)
        return self

    @property
    def evicted_memory_ids(self) -> tuple[str, ...]:
        """Memories leaving the active set, in decision order."""
        return tuple(d.memory_id for d in self.decisions if d.code in _EVICTION_CODES)


def apply_plan(
    candidate: ActiveMemory,
    plan: RebalancePlan,
    summary: MemoryRecord | None = None,
) -> ActiveMemory:
    """Apply a rebalance plan to the post-admission active memory.

    This is the only sanctioned way a memory leaves or enters the active set, and it
    is pure: given the same candidate, plan, and summary it always yields the same
    result, which is what makes a committed cycle replayable.

    Args:
        candidate: Active memory after this cycle's admissions, before eviction.
        plan: The decision produced by the arm's policy for this cycle.
        summary: The generated summary record, required exactly when the plan
            requests compression.

    Raises:
        PolicyError: The plan does not describe this candidate state, or the
            resulting set would exceed the arm's budget.
        LineageError: The summary does not cite exactly the compressed sources.
    """
    if plan.arm_id is not candidate.arm_id or plan.run_id != candidate.run_id:
        msg = f"plan for {plan.arm_id.value} cannot be applied to {candidate.arm_id.value}"
        raise PolicyError(msg)

    rebalanced = candidate.without(plan.evicted_memory_ids)

    if plan.compression is None:
        if summary is not None:
            msg = "a summary record was supplied for a plan that requests no compression"
            raise PolicyError(msg)
    else:
        if summary is None:
            msg = "plan requests compression but no summary record was supplied"
            raise PolicyError(msg)
        if set(summary.source_memory_ids) != set(plan.compression.source_memory_ids):
            msg = f"summary {summary.memory_id} does not cite the compressed sources"
            raise LineageError(msg)
        if summary.token_count > plan.compression.max_summary_tokens:
            msg = (
                f"summary {summary.memory_id} costs {summary.token_count} tokens, "
                f"over the {plan.compression.max_summary_tokens} the policy allowed"
            )
            raise PolicyError(msg)
        if summary.origin_ordinal != plan.compression.target_origin_ordinal:
            msg = f"summary {summary.memory_id} does not occupy the planned ordinal"
            raise PolicyError(msg)
        rebalanced = rebalanced.admit([summary], plan.cycle_index)

    if rebalanced.memory_ids != plan.retained_memory_ids:
        msg = (
            f"applying the plan yielded {rebalanced.memory_ids} but it retains "
            f"{plan.retained_memory_ids}"
        )
        raise PolicyError(msg)
    if not rebalanced.is_within_budget():
        msg = (
            f"{candidate.arm_id.value} would hold {rebalanced.total_tokens} tokens, "
            f"over its budget of {rebalanced.budget_tokens}"
        )
        raise PolicyError(msg)
    return rebalanced
