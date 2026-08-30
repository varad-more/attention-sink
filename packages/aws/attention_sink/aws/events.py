"""The one event this system publishes, and the only shape a consumer may expect.

A cycle commits and says so. Analysis listens. Nothing else is on the bus, because
every additional event is another thing that can be delivered twice, out of order, or
not at all, and the pilot has exactly one asynchronous hand-off.

The payload carries identifiers and counts and no content. An EventBridge event is
retained, archived, and replayable by anyone with bus access, so a journal entry or a
memory in a detail field would be the same leak as one in a log line.
"""

from __future__ import annotations

from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "CYCLE_COMPLETED_DETAIL_TYPE",
    "CYCLE_COMPLETED_SOURCE",
    "CycleCompleted",
]

CYCLE_COMPLETED_SOURCE = "attention-sink.pilot"
"""The event source the rule matches on. One source, so a rule cannot widen by
accident into matching something a later phase publishes."""

CYCLE_COMPLETED_DETAIL_TYPE = "CycleCompleted"


class CycleCompleted(BaseModel):
    """One committed cycle, announced.

    ``committed_arms`` and ``snapshot_hashes`` are here so the consumer can verify it
    is analysing the cycle it was told about rather than trusting the bus: an event
    is a notification, and the store is the truth.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    cycle: int = Field(ge=1)
    run_kind: str = Field(min_length=1)
    committed_arms: tuple[str, ...] = Field(min_length=1)
    snapshot_hashes: dict[str, str]
    """Arm identifier to the digest of that arm's snapshot for this cycle."""

    checkpoint: bool = False
    """Whether this cycle is one of the protocol's interview checkpoints."""

    run_complete: bool = False
    committed_at: str = Field(min_length=1)

    @classmethod
    def from_detail(cls, detail: Any) -> Self:
        """Parse an EventBridge detail into this record.

        Raises:
            ValidationError: The detail is not a cycle-completed event. Treated as a
                permanent failure by the handler, because a redelivery of a malformed
                event is malformed too and belongs in the dead-letter queue.
        """
        return cls.model_validate(detail)
