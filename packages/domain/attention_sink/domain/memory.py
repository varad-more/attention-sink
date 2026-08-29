"""The immutable unit of episodic memory.

A ``MemoryRecord`` is written once and never mutated. Everything mutable about a
memory -- whether it is still active, when it was last cited, how often -- lives in
the ``ActiveMemory`` projection instead, so that the historical record of what an
agent once knew can never be rewritten by later events.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from attention_sink.domain.enums import ArmId, MemoryKind

__all__ = ["MemoryRecord", "make_memory_id"]


def make_memory_id(arm_id: ArmId, origin_ordinal: int) -> str:
    """Build the deterministic memory identifier for an arm-local insertion slot.

    Identifiers are readable rather than opaque because they are the primary handle
    used in provenance and citation-audit output. ``origin_ordinal`` is unique and
    strictly increasing within an arm, so the identifier is unique within a run.
    """
    if origin_ordinal < 0:
        msg = f"origin_ordinal must be non-negative, got {origin_ordinal}"
        raise ValueError(msg)
    return f"mem_{arm_id.value}_{origin_ordinal:06d}"


class MemoryRecord(BaseModel):
    """One immutable episodic memory belonging to exactly one arm of one run.

    Invariants enforced here:

    * ``kind == SUMMARY`` if and only if ``source_memory_ids`` is non-empty. This is
      the lineage guarantee: every compression is traceable to what it compressed.
    * ``created_at`` is timezone-aware UTC.
    * ``token_count`` is the cost under the run's recorded token-counter version; it
      is stored rather than recomputed so that historical budgets stay auditable
      even if the counter version changes in a later run.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    memory_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    kind: MemoryKind
    content: str = Field(min_length=1)
    token_count: int = Field(ge=1)
    created_cycle: int = Field(ge=0)
    origin_ordinal: int = Field(ge=0)
    source_memory_ids: tuple[str, ...] = ()
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def _require_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            msg = "created_at must be timezone-aware"
            raise ValueError(msg)
        return value.astimezone(UTC)

    @model_validator(mode="after")
    def _require_lineage_iff_summary(self) -> Self:
        is_summary = self.kind is MemoryKind.SUMMARY
        has_sources = bool(self.source_memory_ids)
        if is_summary and not has_sources:
            msg = f"summary memory {self.memory_id} must record its source memories"
            raise ValueError(msg)
        if not is_summary and has_sources:
            msg = f"non-summary memory {self.memory_id} must not claim source memories"
            raise ValueError(msg)
        if len(set(self.source_memory_ids)) != len(self.source_memory_ids):
            msg = f"summary memory {self.memory_id} lists a source more than once"
            raise ValueError(msg)
        return self
