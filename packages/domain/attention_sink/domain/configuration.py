"""The frozen parameters of one run.

Everything here is decided before cycle 0 and never changes. A parameter that
changed mid-run would make the arms incomparable with each other and the run
incomparable with itself, so these models exist to be hashed into the manifest and
then left alone.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.enums import CANONICAL_ARMS, ArmId, MemoryKind
from attention_sink.domain.errors import PolicyError, UnsatisfiableBudgetError
from attention_sink.domain.identifiers import (
    MemoryId,
    PromptVersion,
    ProtocolVersion,
    RunId,
    UtcTimestamp,
)
from attention_sink.domain.memory import MIN_SUMMARY_SOURCES, Memory
from attention_sink.domain.tokens import TokenBudget

__all__ = [
    "DEFAULT_CITATION_DECAY",
    "DEFAULT_RECENCY_RESERVE",
    "HeavyHitterConfig",
    "InferenceParameters",
    "ModelConfiguration",
    "PinnedOriginConfig",
    "PolicyConfiguration",
    "RunConfiguration",
    "SummarizationConfig",
]

DEFAULT_CITATION_DECAY = 0.90
"""Per-cycle discount on citation weight.

Chosen so a memory's influence halves after roughly seven uncited cycles: long
enough that a genuinely important memory survives a quiet stretch, short enough
that the arm is measurably different from one that counts citations forever.
"""

DEFAULT_RECENCY_RESERVE = 2
"""Newest active memories the citation-weighted arm protects from eviction.

Without a reserve the arm would evict every new memory on sight: a memory that has
not yet been shown to the writer has a citation score of zero by construction, not
because it turned out to be worthless.
"""


class InferenceParameters(BaseModel):
    """Decoding settings applied identically to every arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    temperature: float = Field(ge=0.0, le=2.0)
    top_p: float = Field(gt=0.0, le=1.0)
    max_output_tokens: int = Field(gt=0)


class ModelConfiguration(BaseModel):
    """The models and decoding settings a run is defined by.

    Distinct from the runtime configuration in ``attention_sink.model_gateway``,
    which also resolves a Region and credentials. This one holds only the
    experimental parameters -- the values that make two runs different experiments
    rather than the same experiment on different infrastructure.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    writer_model_id: str = Field(min_length=1, max_length=256)
    auditor_model_id: str = Field(min_length=1, max_length=256)
    judge_model_id: str = Field(min_length=1, max_length=256)
    summary_model_id: str = Field(min_length=1, max_length=256)
    embedding_model_id: str = Field(min_length=1, max_length=256)
    inference: InferenceParameters


class HeavyHitterConfig(BaseModel):
    """Parameters of the citation-weighted arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    recency_reserve: int = Field(default=DEFAULT_RECENCY_RESERVE, ge=0)


class PinnedOriginConfig(BaseModel):
    """Which memory the pinned-origin arm may never forget.

    Optional here and resolved at run creation. When unset the arm falls back to the
    ``pinned`` flag on the memories themselves, so a run can pin its origin either
    by naming it once or by marking it in the seed set.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    pinned_memory_id: MemoryId | None = None


class SummarizationConfig(BaseModel):
    """Parameters of the lossy summarising arm."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    summary_target_token_limit: int = Field(default=64, gt=0)
    """Ceiling on what a summary may cost. Charged against the same budget."""

    safety_margin_tokens: int = Field(default=0, ge=0)
    """Headroom a compression must leave below the budget.

    Without it the arm plans a compression that lands exactly on the ceiling, and
    the next cycle's admission immediately forces another one.
    """

    min_sources: int = Field(default=MIN_SUMMARY_SOURCES, ge=MIN_SUMMARY_SOURCES)
    fifo_fallback_enabled: bool = True
    """Whether an infeasible compression falls back to oldest-first eviction.

    Disabling it makes the arm raise instead, which is the honest choice for a
    protocol that would rather stop than silently become a different mechanism.
    """


class PolicyConfiguration(BaseModel):
    """Every policy's parameters, in one serialisable record."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    heavy_hitter: HeavyHitterConfig = HeavyHitterConfig()
    pinned_origin: PinnedOriginConfig = PinnedOriginConfig()
    summarization: SummarizationConfig = SummarizationConfig()


class RunConfiguration(BaseModel):
    """The complete, hashable definition of one canonical run."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    protocol_version: ProtocolVersion
    prompt_version: PromptVersion
    model_configuration: ModelConfiguration
    budget: TokenBudget
    random_seed: str = Field(min_length=8, max_length=256)
    """Application-controlled entropy for the stochastic arm.

    Recorded rather than drawn from the operating system at decision time, which is
    what makes a random eviction replayable instead of merely explainable.
    """

    citation_decay: float = Field(default=DEFAULT_CITATION_DECAY, ge=0.0, le=1.0)
    arms: tuple[ArmId, ...] = CANONICAL_ARMS
    policies: PolicyConfiguration = PolicyConfiguration()
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_distinct_arms(self) -> Self:
        if not self.arms:
            msg = "a run must configure at least one arm"
            raise ValueError(msg)
        if len(set(self.arms)) != len(self.arms):
            msg = "a run cannot configure the same arm twice"
            raise ValueError(msg)
        return self

    def validate_seed_memories(self, seed_memories: Sequence[Memory]) -> None:
        """Check the seed set against the parameters before cycle 0 begins.

        Verifies that the seed set fits the budget, that a configured pinned memory
        exists and is a seed, and that the budget can hold it. All three are
        misconfigurations that would otherwise surface as an unsatisfiable budget
        several cycles in, after the run had already burned model calls.

        Raises:
            PolicyError: The pinned memory is missing or is not a seed memory.
            UnsatisfiableBudgetError: The seed set, or the pinned memory alone,
                cannot fit the budget.
        """
        total = sum(memory.token_count for memory in seed_memories)
        if not self.budget.is_satisfied_by(total):
            msg = (
                f"the seed set costs {total} tokens, over the "
                f"{self.budget.max_active_tokens}-token budget every arm starts from"
            )
            raise UnsatisfiableBudgetError(msg, run_id=self.run_id)

        pinned_id = self.policies.pinned_origin.pinned_memory_id
        if pinned_id is None:
            return
        pinned = next((m for m in seed_memories if m.memory_id == pinned_id), None)
        if pinned is None:
            msg = f"pinned memory {pinned_id} is not present in the seed set"
            raise PolicyError(msg, run_id=self.run_id)
        if pinned.memory_kind is not MemoryKind.SEED:
            msg = f"pinned memory {pinned_id} is a {pinned.memory_kind.value} memory, not a seed"
            raise PolicyError(msg, run_id=self.run_id)
        if not self.budget.is_satisfied_by(pinned.token_count):
            msg = (
                f"pinned memory {pinned_id} costs {pinned.token_count} tokens on its own, "
                f"over the {self.budget.max_active_tokens}-token budget"
            )
            raise UnsatisfiableBudgetError(msg, run_id=self.run_id)
