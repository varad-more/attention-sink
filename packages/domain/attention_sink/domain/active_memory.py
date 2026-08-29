"""The mutable projection of what an agent can currently think with.

``ActiveMemory`` is a value object: every operation returns a new instance. It is
the *only* thing a writer agent is ever shown, which is what makes the eviction
mechanism causally responsible for divergence between arms.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.enums import ArmId
from attention_sink.domain.errors import PolicyError
from attention_sink.domain.memory import MemoryRecord

__all__ = ["ActiveMemory", "ActiveMemoryEntry"]


class ActiveMemoryEntry(BaseModel):
    """A memory that is currently active, plus the usage statistics policies read.

    ``citation_count`` and ``last_cited_cycle`` only ever advance on *verified*
    citations -- an auditor confirmed the thought actually used the memory. A model
    asserting that it remembered something is not evidence that it did.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    record: MemoryRecord
    admitted_cycle: int = Field(ge=0)
    last_cited_cycle: int | None = None
    citation_count: int = Field(default=0, ge=0)

    @property
    def memory_id(self) -> str:
        """Identifier of the underlying immutable record."""
        return self.record.memory_id

    @property
    def origin_ordinal(self) -> int:
        """Arm-local insertion order. Unique, so it is a total tie-break key."""
        return self.record.origin_ordinal

    @property
    def token_count(self) -> int:
        """Budget-token cost of holding this memory active."""
        return self.record.token_count

    @property
    def last_used_cycle(self) -> int:
        """Cycle of the most recent *use*, treating admission as the first use.

        Without this fallback a never-cited memory would have no recency at all and
        LRU ordering would be undefined for it.
        """
        return self.admitted_cycle if self.last_cited_cycle is None else self.last_cited_cycle

    def cited_at(self, cycle_index: int) -> ActiveMemoryEntry:
        """Return a copy recording one verified citation in ``cycle_index``."""
        return self.model_copy(
            update={
                "citation_count": self.citation_count + 1,
                "last_cited_cycle": max(cycle_index, self.last_used_cycle),
            }
        )


class ActiveMemory(BaseModel):
    """The ordered set of memories one arm may currently reason over.

    Entries are held in ascending ``origin_ordinal`` order. That order is the
    canonical presentation order given to the writer and the deterministic
    tie-break used by every policy, so it is validated rather than assumed.

    The budget is deliberately *not* enforced by this model: a rebalance passes
    through a legitimately over-budget intermediate state between admission and
    eviction. Use :meth:`is_within_budget` at commit boundaries instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    budget_tokens: int = Field(gt=0)
    entries: tuple[ActiveMemoryEntry, ...] = ()
    next_origin_ordinal: int = Field(default=0, ge=0)
    """Monotonic arm-local ordinal allocator.

    Stored rather than derived from ``entries`` because evicting the newest memory
    must not free its slot for reuse: ordinals are the basis of memory identifiers,
    which have to stay unique across the whole life of the run.
    """

    @model_validator(mode="after")
    def _require_unique_and_ordered(self) -> Self:
        ordinals = [entry.origin_ordinal for entry in self.entries]
        if ordinals != sorted(ordinals):
            msg = f"{self.arm_id.value} active memory is not in origin order"
            raise ValueError(msg)
        if len(set(ordinals)) != len(ordinals):
            msg = f"{self.arm_id.value} active memory has duplicate origin ordinals"
            raise ValueError(msg)
        for entry in self.entries:
            if entry.record.arm_id is not self.arm_id:
                msg = f"memory {entry.memory_id} belongs to {entry.record.arm_id.value}"
                raise ValueError(msg)
            if entry.record.run_id != self.run_id:
                msg = f"memory {entry.memory_id} belongs to run {entry.record.run_id}"
                raise ValueError(msg)
        if ordinals and self.next_origin_ordinal <= ordinals[-1]:
            msg = (
                f"{self.arm_id.value} next ordinal {self.next_origin_ordinal} would "
                f"reuse the slot of active ordinal {ordinals[-1]}"
            )
            raise ValueError(msg)
        return self

    @property
    def total_tokens(self) -> int:
        """Sum of the budget-token cost of every active memory."""
        return sum(entry.token_count for entry in self.entries)

    @property
    def memory_ids(self) -> tuple[str, ...]:
        """Active memory identifiers in canonical presentation order."""
        return tuple(entry.memory_id for entry in self.entries)

    def is_within_budget(self) -> bool:
        """Whether the active set currently satisfies the arm's token budget."""
        return self.total_tokens <= self.budget_tokens

    def get(self, memory_id: str) -> ActiveMemoryEntry | None:
        """Return the active entry for ``memory_id``, or ``None`` if not active."""
        return next((e for e in self.entries if e.memory_id == memory_id), None)

    def admit(self, records: Sequence[MemoryRecord], cycle_index: int) -> ActiveMemory:
        """Return a copy with ``records`` appended as newly admitted entries.

        The result may exceed the budget; that is the input a rebalance policy is
        expected to resolve.
        """
        if not records:
            return self
        existing = set(self.memory_ids)
        floor = self.next_origin_ordinal
        for record in records:
            if record.memory_id in existing:
                msg = f"memory {record.memory_id} is already active"
                raise PolicyError(msg)
            if record.origin_ordinal < floor:
                msg = (
                    f"memory {record.memory_id} has ordinal {record.origin_ordinal} "
                    f"below the next free ordinal {floor}"
                )
                raise PolicyError(msg)
            floor = record.origin_ordinal + 1
        admitted = tuple(
            ActiveMemoryEntry(record=record, admitted_cycle=cycle_index) for record in records
        )
        return self.model_copy(
            update={"entries": self.entries + admitted, "next_origin_ordinal": floor}
        )

    def record_citations(self, memory_ids: Iterable[str], cycle_index: int) -> ActiveMemory:
        """Return a copy with verified citations applied to the named memories.

        Identifiers that are not active are ignored: an auditor may legitimately
        name a memory that a concurrent rebalance has already evicted, and a
        citation of an inactive memory must not resurrect it.
        """
        cited = set(memory_ids)
        if not cited:
            return self
        updated = tuple(
            entry.cited_at(cycle_index) if entry.memory_id in cited else entry
            for entry in self.entries
        )
        return self.model_copy(update={"entries": updated})

    def without(self, memory_ids: Iterable[str]) -> ActiveMemory:
        """Return a copy with the named memories removed from the active set."""
        removed = set(memory_ids)
        if not removed:
            return self
        kept = tuple(entry for entry in self.entries if entry.memory_id not in removed)
        if len(kept) + len(removed) != len(self.entries):
            unknown = removed - set(self.memory_ids)
            msg = f"cannot evict memories that are not active: {sorted(unknown)}"
            raise PolicyError(msg)
        return self.model_copy(update={"entries": kept})
