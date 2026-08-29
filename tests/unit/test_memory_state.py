"""State transitions: admission, citation bookkeeping, and applying a decision."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from attention_sink.domain import (
    ArmId,
    CitationSource,
    MemoryKind,
    MemoryState,
    MemoryStatus,
    PolicyError,
    StateError,
    VerifiedCitation,
    state_hash,
)
from attention_sink.policies import FifoPolicy
from tests.factories import RUN_ID, MemorySpec, budget, build_state, context, uniform_state


def a_citation(memory_id: str, *, source: CitationSource, cycle: int = 5) -> VerifiedCitation:
    return VerifiedCitation(
        run_id=RUN_ID,
        arm_id=ArmId.ARM_LRU,
        cycle=cycle,
        memory_id=memory_id,
        source=source,
        auditor_version="auditor-v1",
        evidence="the thought reuses the phrase verbatim",
    )


def test_empty_state_is_coherent() -> None:
    state = MemoryState(run_id=RUN_ID, arm_id=ArmId.ARM_FIFO)
    assert state.active_memories == ()
    assert state.active_tokens == 0
    assert state.next_memory_id() == "mem_arm_fifo_000000"
    assert state.state_hash == state_hash([])


def test_active_set_excludes_retired_memories() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=3)
    decision = FifoPolicy().rebalance(state, budget(20), context(ArmId.ARM_FIFO))
    after = state.apply(decision)
    assert len(after.memories) == 3
    assert len(after.active_memories) == 2
    retired = after.get(decision.retired_memory_ids[0])
    assert retired is not None
    assert retired.status is MemoryStatus.EVICTED


def test_admitting_a_known_memory_is_refused() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=1)
    with pytest.raises(StateError, match="already known"):
        state.admit([state.memories[0]])


def test_admitting_a_retired_memory_is_refused() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=1)
    retired = state.mint(
        text="new", token_count=3, memory_kind=MemoryKind.GENERATED, cycle=1
    ).retire(status=MemoryStatus.EVICTED, cycle=2)
    with pytest.raises(StateError, match="cannot be admitted"):
        state.admit([retired])


def test_creation_slots_are_never_reused() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=3)
    decision = FifoPolicy().rebalance(state, budget(10), context(ArmId.ARM_FIFO))
    after = state.apply(decision)
    assert after.next_creation_sequence == 3
    assert after.next_memory_id() == "mem_arm_fifo_000003"


def test_state_rejects_out_of_order_creation_sequences() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=2)
    scrambled = state.model_dump()
    scrambled["memories"] = list(reversed(scrambled["memories"]))
    with pytest.raises(ValidationError, match="not in creation order"):
        MemoryState.model_validate(scrambled)


def test_state_rejects_a_memory_from_another_arm() -> None:
    fifo = uniform_state(ArmId.ARM_FIFO, count=1)
    foreign = fifo.model_dump()
    foreign["arm_id"] = ArmId.ARM_LRU
    with pytest.raises(ValidationError, match="belongs to another run or arm"):
        MemoryState.model_validate(foreign)


def test_state_rejects_a_summary_with_unknown_parents() -> None:
    state = uniform_state(ArmId.ARM_SUMMARY, count=2)
    orphan = state.mint(
        text="a summary of memories that are not here",
        token_count=4,
        memory_kind=MemoryKind.SUMMARY,
        cycle=3,
        parent_memory_ids=("mem_arm_summary_000090", "mem_arm_summary_000091"),
    )
    with pytest.raises(ValidationError, match="unknown parents"):
        state.admit([orphan])


def test_writer_citations_update_state() -> None:
    state = uniform_state(ArmId.ARM_LRU, count=2)
    target = state.active_memory_ids[0]
    updated = state.record_cycle_citations(
        [a_citation(target, source=CitationSource.WRITER)], cycle=5, decay=0.9
    )
    memory = updated.get(target)
    assert memory is not None
    assert memory.citation_count == 1
    assert memory.last_verified_citation_cycle == 5
    assert memory.discounted_citation_score == pytest.approx(1.0)


@pytest.mark.parametrize("source", [CitationSource.INTERVIEW, CitationSource.EVALUATION])
def test_read_only_probes_never_update_state(source: CitationSource) -> None:
    state = uniform_state(ArmId.ARM_LRU, count=2)
    target = state.active_memory_ids[0]
    updated = state.record_cycle_citations([a_citation(target, source=source)], cycle=5, decay=0.9)
    assert updated == state


def test_citations_for_another_arm_are_ignored() -> None:
    state = uniform_state(ArmId.ARM_LRU, count=1)
    citation = a_citation(state.active_memory_ids[0], source=CitationSource.WRITER).model_copy(
        update={"arm_id": ArmId.ARM_FIFO}
    )
    assert state.record_cycle_citations([citation], cycle=5, decay=0.9) == state


def test_decay_applies_to_every_active_memory_not_only_cited_ones() -> None:
    state = build_state(
        ArmId.ARM_HEAVY,
        [MemorySpec(tokens=5, cycle=0, score=10.0), MemorySpec(tokens=5, cycle=1, score=4.0)],
    )
    updated = state.record_cycle_citations([], cycle=5, decay=0.5)
    scores = [m.discounted_citation_score for m in updated.active_memories]
    assert scores == pytest.approx([5.0, 2.0])


def test_apply_refuses_a_decision_from_another_arm() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=2)
    decision = FifoPolicy().rebalance(state, budget(10), context(ArmId.ARM_FIFO))
    other = uniform_state(ArmId.ARM_LRU, count=2)
    with pytest.raises(PolicyError, match="cannot be applied"):
        other.apply(decision)


def test_apply_refuses_to_retire_an_inactive_memory() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=3)
    decision = FifoPolicy().rebalance(state, budget(10), context(ArmId.ARM_FIFO))
    once = state.apply(decision)
    with pytest.raises(PolicyError, match="not active"):
        once.apply(decision)


def test_apply_refuses_to_retire_a_pinned_memory() -> None:
    state = build_state(
        ArmId.ARM_FIFO, [MemorySpec(tokens=10, cycle=0), MemorySpec(tokens=10, cycle=1)]
    )
    decision = FifoPolicy().rebalance(state, budget(10), context(ArmId.ARM_FIFO))
    pinned = MemoryState.model_validate(
        {
            **state.model_dump(),
            "memories": [
                {**state.memories[0].model_dump(), "pinned": True},
                state.memories[1].model_dump(),
            ],
        }
    )
    with pytest.raises(PolicyError, match="cannot retire pinned memory"):
        pinned.apply(decision)


def test_state_round_trips_through_serialisation() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=3)
    assert MemoryState.model_validate(state.model_dump()) == state
    assert MemoryState.model_validate_json(state.model_dump_json()) == state


def test_state_hash_tracks_the_active_set_and_its_order() -> None:
    state = uniform_state(ArmId.ARM_FIFO, count=3)
    after = state.apply(FifoPolicy().rebalance(state, budget(20), context(ArmId.ARM_FIFO)))
    assert state.state_hash != after.state_hash
    assert after.state_hash == state_hash(after.active_memory_ids)
