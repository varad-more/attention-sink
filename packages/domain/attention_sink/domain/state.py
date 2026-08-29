"""Everything one arm remembers, and the transitions that are allowed to change it.

``MemoryState`` holds every memory the arm has ever had, retired ones included.
Keeping the retired records rather than deleting them is what makes lineage
resolvable and what lets a reader ask what an arm *used* to know -- the question the
experiment exists to answer.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.domain.citations import VerifiedCitation
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, MemoryKind, MemoryStatus
from attention_sink.domain.errors import ErrorContext, LineageError, PolicyError, StateError
from attention_sink.domain.hashing import state_hash
from attention_sink.domain.identifiers import MemoryId, RunId, StimulusId
from attention_sink.domain.memory import Memory, MemoryLineageEdge, make_memory_id

__all__ = ["MemoryState"]


class MemoryState(BaseModel):
    """The complete memory of one arm of one run.

    Every operation returns a new, revalidated instance; nothing here mutates. That
    is not stylistic -- a policy is handed this object and must be provably unable to
    change it, or an arm could influence its own inputs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: RunId
    arm_id: ArmId
    memories: tuple[Memory, ...] = ()
    lineage_edges: tuple[MemoryLineageEdge, ...] = ()
    next_creation_sequence: int = Field(default=0, ge=0)
    """Monotonic allocator for arm-local creation slots.

    Stored rather than derived, because retiring the newest memory must not free its
    slot: identifiers are built from this number and have to stay unique for the
    whole life of the run.
    """

    @model_validator(mode="after")
    def _check_invariants(self) -> Self:
        ids = [memory.memory_id for memory in self.memories]
        if len(set(ids)) != len(ids):
            duplicated = sorted({mid for mid, n in Counter(ids).items() if n > 1})
            msg = f"{self.arm_id.value} holds duplicate memory ids: {duplicated}"
            raise ValueError(msg)

        sequences = [memory.creation_sequence for memory in self.memories]
        if sequences != sorted(sequences):
            msg = f"{self.arm_id.value} memories are not in creation order"
            raise ValueError(msg)
        if len(set(sequences)) != len(sequences):
            msg = f"{self.arm_id.value} memories reuse a creation sequence"
            raise ValueError(msg)
        if sequences and self.next_creation_sequence <= sequences[-1]:
            msg = (
                f"{self.arm_id.value} next creation sequence {self.next_creation_sequence} "
                f"would reuse the slot of memory {sequences[-1]}"
            )
            raise ValueError(msg)

        known = set(ids)
        for memory in self.memories:
            if memory.run_id != self.run_id or memory.arm_id is not self.arm_id:
                msg = f"memory {memory.memory_id} belongs to another run or arm"
                raise ValueError(msg)
            missing = set(memory.parent_memory_ids) - known
            if missing:
                msg = f"summary {memory.memory_id} names unknown parents: {sorted(missing)}"
                raise ValueError(msg)
        for edge in self.lineage_edges:
            unknown = {edge.parent_memory_id, edge.child_memory_id} - known
            if unknown:
                msg = f"lineage edge names unknown memories: {sorted(unknown)}"
                raise ValueError(msg)
        return self

    # ---------------------------------------------------------------- queries

    @property
    def active_memories(self) -> tuple[Memory, ...]:
        """Memories the writer can currently reason over, in creation order."""
        return tuple(memory for memory in self.memories if memory.is_active)

    @property
    def active_memory_ids(self) -> tuple[MemoryId, ...]:
        """Identifiers of the active set, in the order it is presented."""
        return tuple(memory.memory_id for memory in self.active_memories)

    @property
    def active_tokens(self) -> int:
        """Budget-token cost of the active set."""
        return sum(memory.token_count for memory in self.active_memories)

    @property
    def state_hash(self) -> str:
        """Digest of the active set, for replay verification."""
        return state_hash(self.active_memory_ids)

    def get(self, memory_id: str) -> Memory | None:
        """Return the memory with ``memory_id``, active or retired, if it exists."""
        return next((m for m in self.memories if m.memory_id == memory_id), None)

    def next_memory_id(self) -> MemoryId:
        """The identifier the next minted memory will take."""
        return make_memory_id(self.arm_id, self.next_creation_sequence)

    # ------------------------------------------------------------ transitions

    def mint(
        self,
        *,
        text: str,
        token_count: int,
        memory_kind: MemoryKind,
        cycle: int,
        source_stimulus_id: StimulusId | None = None,
        parent_memory_ids: Sequence[MemoryId] = (),
        pinned: bool = False,
    ) -> Memory:
        """Build the next memory for this arm without admitting it.

        Separated from :meth:`admit` because the summarising arm has to know the
        identifier a summary *will* take before the text exists.
        """
        return Memory(
            memory_id=self.next_memory_id(),
            run_id=self.run_id,
            arm_id=self.arm_id,
            text=text,
            token_count=token_count,
            memory_kind=memory_kind,
            birth_cycle=cycle,
            source_stimulus_id=source_stimulus_id,
            parent_memory_ids=tuple(parent_memory_ids),
            pinned=pinned,
            creation_sequence=self.next_creation_sequence,
        )

    def admit(self, memories: Sequence[Memory]) -> MemoryState:
        """Return a copy with ``memories`` appended to the active set.

        The result may exceed the budget: that over-budget intermediate state is
        exactly the input a rebalance policy exists to resolve.

        Raises:
            StateError: A memory is already known, is not active, or claims a
                creation slot that has already been allocated.
        """
        if not memories:
            return self
        known = {memory.memory_id for memory in self.memories}
        cursor = self.next_creation_sequence
        for memory in memories:
            if memory.memory_id in known:
                msg = f"memory {memory.memory_id} is already known to this arm"
                raise StateError(msg, run_id=self.run_id, arm_id=self.arm_id.value)
            if not memory.is_active:
                msg = f"memory {memory.memory_id} is {memory.status.value} and cannot be admitted"
                raise StateError(msg, run_id=self.run_id, arm_id=self.arm_id.value)
            if memory.creation_sequence < cursor:
                msg = (
                    f"memory {memory.memory_id} claims creation slot "
                    f"{memory.creation_sequence}, below the next free slot {cursor}"
                )
                raise StateError(msg, run_id=self.run_id, arm_id=self.arm_id.value)
            cursor = memory.creation_sequence + 1
        return MemoryState.model_validate(
            {
                **self.model_dump(),
                "memories": [*self.memories, *memories],
                "next_creation_sequence": cursor,
            }
        )

    def record_cycle_citations(
        self,
        citations: Iterable[VerifiedCitation],
        *,
        cycle: int,
        decay: float,
    ) -> MemoryState:
        """Fold one cycle of verified citations into every active memory's score.

        Applies ``new = decay * previous + writer_citations_this_cycle`` to every
        active memory, whether or not it was cited, because a decay that only ran on
        cited memories would not be a decay.

        Citations from interviews and evaluations are discarded here rather than
        filtered by the caller: a read-only probe must not be able to change what an
        arm remembers, and centralising that makes it one rule instead of a
        convention every call site has to honour.
        """
        counts = Counter(
            citation.memory_id
            for citation in citations
            if citation.updates_memory_state
            and citation.run_id == self.run_id
            and citation.arm_id is self.arm_id
        )
        updated = tuple(
            memory
            if not memory.is_active
            or (counts[memory.memory_id] == 0 and not memory.discounted_citation_score)
            else memory.with_citation_cycle(
                cycle=cycle, citations=counts[memory.memory_id], decay=decay
            )
            for memory in self.memories
        )
        return MemoryState.model_validate({**self.model_dump(), "memories": list(updated)})

    def apply(self, decision: PolicyDecision) -> MemoryState:
        """Apply a policy decision, producing the state the next cycle starts from.

        The only sanctioned way a memory leaves or enters the active set. Pure:
        given the same state and decision it always yields the same result, which is
        what makes a committed cycle replayable.

        Raises:
            PolicyError: The decision does not describe this state, or applying it
                would not produce the active set the decision claims.
            LineageError: A created summary does not name the sources it compressed.
        """
        ctx = ErrorContext(
            run_id=self.run_id,
            arm_id=self.arm_id.value,
            cycle=decision.cycle,
            policy_version=decision.policy_version,
        )
        if decision.run_id != self.run_id or decision.arm_id is not self.arm_id:
            msg = f"decision for {decision.arm_id.value} cannot be applied to {self.arm_id.value}"
            raise PolicyError(msg, **ctx)

        by_id = {memory.memory_id: memory for memory in self.memories}
        for retirement in decision.retirements:
            target = by_id.get(retirement.memory_id)
            if target is None or not target.is_active:
                msg = f"cannot retire {retirement.memory_id}: it is not active"
                raise PolicyError(msg, **ctx)
            if target.pinned:
                msg = f"cannot retire pinned memory {retirement.memory_id}"
                raise PolicyError(msg, **ctx)
            by_id[retirement.memory_id] = target.retire(
                status=retirement.status, cycle=decision.cycle
            )

        for created in decision.created_memories:
            if created.memory_kind is MemoryKind.SUMMARY:
                compressed = {
                    r.memory_id for r in decision.retirements if r.status is MemoryStatus.COMPRESSED
                }
                if set(created.parent_memory_ids) != compressed:
                    msg = (
                        f"summary {created.memory_id} names parents "
                        f"{sorted(created.parent_memory_ids)} but the decision compressed "
                        f"{sorted(compressed)}"
                    )
                    raise LineageError(msg, **ctx)

        # Order matters: a lineage edge names the memory it points at, so the created
        # summary has to exist before the edges that descend into it are recorded.
        retired_state = MemoryState.model_validate(
            {
                **self.model_dump(),
                "memories": [memory.model_dump() for memory in by_id.values()],
            }
        )
        admitted = retired_state.admit(decision.created_memories)
        rebuilt = MemoryState.model_validate(
            {
                **admitted.model_dump(),
                "lineage_edges": [
                    *(edge.model_dump() for edge in admitted.lineage_edges),
                    *(edge.model_dump() for edge in decision.lineage_edges),
                ],
            }
        )

        if rebuilt.active_memory_ids != decision.kept_memory_ids:
            msg = (
                f"applying the decision yielded {rebuilt.active_memory_ids} but it keeps "
                f"{decision.kept_memory_ids}"
            )
            raise PolicyError(msg, **ctx)
        if rebuilt.active_tokens != decision.tokens_after:
            msg = (
                f"applying the decision costs {rebuilt.active_tokens} tokens but it "
                f"projected {decision.tokens_after}"
            )
            raise PolicyError(msg, **ctx)
        if decision.is_final and rebuilt.active_tokens > decision.budget_tokens:
            msg = (
                f"{self.arm_id.value} would hold {rebuilt.active_tokens} tokens, over its "
                f"budget of {decision.budget_tokens}"
            )
            raise PolicyError(msg, **ctx)
        return rebuilt
