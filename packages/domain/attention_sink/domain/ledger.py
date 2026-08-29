"""Append-only records: what happened, and what any score was computed from.

Nothing in this module is ever updated in place. A correction is a new event that
supersedes an earlier one, so the history of a run stays readable as the sequence
of things that were actually believed at the time.
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from attention_sink.domain.enums import ArmId, LedgerEventType
from attention_sink.domain.identifiers import (
    CycleNumber,
    EventId,
    MemoryId,
    RunId,
    UtcTimestamp,
    Version,
)

__all__ = ["LedgerEvent", "MetricEvidence"]


class LedgerEvent(BaseModel):
    """One immutable fact about a run.

    ``sequence`` orders events within a run and is the concurrency token: two
    writers that both believe they are producing sequence *n* cannot both win, which
    is what stops a retried step from committing a cycle twice.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    event_id: EventId
    run_id: RunId
    sequence: int = Field(ge=0)
    event_type: LedgerEventType
    occurred_at: UtcTimestamp
    arm_id: ArmId | None = None
    cycle: CycleNumber | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    payload_hash: str = Field(min_length=1)
    """Digest of the payload as it was written. Detects an edited ledger row."""

    idempotency_key: str | None = None
    """Caller-supplied key that makes a retry of the same intent a no-op."""


class MetricEvidence(BaseModel):
    """A score, and everything needed to argue with it.

    A number on its own is not a finding. Storing the evaluator version, the
    calculation version, and the memories the judgement rested on is what lets a
    disputed score be recomputed or re-argued instead of merely re-asserted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    cycle: CycleNumber
    metric_name: str = Field(min_length=1, max_length=128)
    value: float
    evaluator_version: Version
    calculation_version: Version
    cited_memory_ids: tuple[MemoryId, ...] = ()
    rationale: str = Field(min_length=1)
    computed_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_distinct_evidence(self) -> Self:
        if len(set(self.cited_memory_ids)) != len(self.cited_memory_ids):
            msg = f"{self.metric_name} cites a memory more than once as evidence"
            raise ValueError(msg)
        return self
