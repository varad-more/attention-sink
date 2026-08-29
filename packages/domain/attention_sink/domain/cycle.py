"""What a policy is told about the cycle it is deciding, and what is recorded after.

:class:`CycleContext` is deliberately narrow. It excludes the arm's public name, the
state of every other arm, the run's predictions, and every metric, because a policy
that could see any of those could optimise for the thing being measured.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, CycleStatus
from attention_sink.domain.identifiers import (
    CycleNumber,
    MemoryId,
    PromptVersion,
    ProtocolVersion,
    RunId,
    StimulusId,
    UtcTimestamp,
    Version,
)

__all__ = ["CycleContext", "CycleSnapshot"]


class CycleContext(BaseModel):
    """Everything a policy is permitted to know when deciding one arm-cycle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    stimulus_id: StimulusId
    protocol_version: ProtocolVersion
    prompt_version: PromptVersion
    run_random_seed: str = Field(min_length=8, max_length=256)


class CycleSnapshot(BaseModel):
    """The immutable record of what one arm held, and decided, in one cycle.

    Written once at commit. ``state_hash`` is what a replay is checked against: if a
    reconstruction of the run reaches a different active set, the mismatch surfaces
    here rather than in a metric several steps downstream.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    stimulus_id: StimulusId
    status: CycleStatus
    protocol_version: ProtocolVersion
    prompt_version: PromptVersion
    policy_version: Version
    active_memory_ids: tuple[MemoryId, ...]
    active_tokens: int = Field(ge=0)
    budget_tokens: int = Field(gt=0)
    state_hash: str = Field(min_length=1)
    decision: PolicyDecision
    committed_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_matching_decision(self) -> Self:
        if (
            self.decision.run_id != self.run_id
            or self.decision.arm_id is not self.arm_id
            or self.decision.cycle != self.cycle
        ):
            msg = f"snapshot for {self.arm_id.value} cycle {self.cycle} carries another decision"
            raise ValueError(msg)
        if self.status is CycleStatus.COMMITTED:
            if not self.decision.is_final:
                msg = "a committed cycle cannot carry a decision that still awaits a summary"
                raise ValueError(msg)
            if self.active_memory_ids != self.decision.kept_memory_ids:
                msg = "the snapshot's active set does not match what the decision kept"
                raise ValueError(msg)
            if self.active_tokens > self.budget_tokens:
                msg = (
                    f"committed cycle holds {self.active_tokens} tokens, over the "
                    f"{self.budget_tokens}-token budget"
                )
                raise ValueError(msg)
        return self
