"""What a writer claimed it remembered, and what an auditor confirmed it used.

The two are separate types on purpose. A model asserting that it drew on a memory
is not evidence that it did, and the whole citation-driven half of the experiment
would be circular if the claim and the verification were the same record.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.enums import ArmId, CitationSource
from attention_sink.domain.identifiers import CycleNumber, MemoryId, RunId, Version

__all__ = ["CitationClaim", "VerifiedCitation"]


class CitationClaim(BaseModel):
    """An unverified assertion by the writer that it used a particular memory.

    Never affects policy state. It exists so that the auditor's rejections are
    themselves recorded: how often an arm claims memories it did not use is a
    finding, not noise to be discarded.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    memory_id: MemoryId
    claim_index: int = Field(ge=0)
    quoted_span: str | None = None
    """The span of the memory the writer says it used, when it named one."""


class VerifiedCitation(BaseModel):
    """An auditor's confirmation that a thought actually used a memory.

    ``source`` is load-bearing. Only :attr:`CitationSource.WRITER` citations may
    change memory state; interview and evaluation citations are recorded for
    analysis and then ignored by every policy. Without that split, measuring an arm
    would alter what it goes on to remember.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    memory_id: MemoryId
    source: CitationSource
    auditor_version: Version
    evidence: str = Field(min_length=1)
    """The auditor's stated grounds. Stored so a score can be re-argued later."""

    @property
    def updates_memory_state(self) -> bool:
        """Whether this citation is allowed to change policy-visible state."""
        return self.source is CitationSource.WRITER

    @model_validator(mode="after")
    def _require_writer_evidence(self) -> Self:
        if self.updates_memory_state and not self.evidence.strip():
            msg = f"writer citation of {self.memory_id} carries no evidence"
            raise ValueError(msg)
        return self
