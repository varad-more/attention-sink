"""Memory record invariants: the ones no other layer is allowed to assume."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from attention_sink.domain import (
    ArmId,
    LineageRelation,
    Memory,
    MemoryKind,
    MemoryLineageEdge,
    MemoryStatus,
    content_hash,
    make_memory_id,
)
from tests.factories import RUN_ID


def a_memory(**changes: object) -> Memory:
    base: dict[str, object] = {
        "memory_id": "mem_arm_fifo_000000",
        "run_id": RUN_ID,
        "arm_id": ArmId.ARM_FIFO,
        "text": "the light through the window has moved",
        "token_count": 9,
        "memory_kind": MemoryKind.GENERATED,
        "birth_cycle": 3,
        "creation_sequence": 0,
    }
    return Memory(**{**base, **changes})  # type: ignore[arg-type]


def test_content_hash_is_filled_when_omitted() -> None:
    memory = a_memory()
    assert memory.content_hash == content_hash(memory.text)


def test_content_hash_is_verified_when_supplied() -> None:
    with pytest.raises(ValidationError, match="content hash does not match"):
        a_memory(content_hash="sha256:" + "0" * 64)


def test_edited_text_fails_to_load() -> None:
    stored = a_memory().model_dump()
    stored["text"] = "a quietly different memory"
    with pytest.raises(ValidationError, match="content hash does not match"):
        Memory.model_validate(stored)


@pytest.mark.parametrize("sources", [(), ("mem_arm_fifo_000001",)])
def test_summary_needs_at_least_two_parents(sources: tuple[str, ...]) -> None:
    with pytest.raises(ValidationError, match="at least 2 are required|sources"):
        a_memory(memory_kind=MemoryKind.SUMMARY, parent_memory_ids=sources)


def test_non_summary_may_not_claim_parents() -> None:
    with pytest.raises(ValidationError, match="must not claim parent"):
        a_memory(parent_memory_ids=("mem_arm_fifo_000001", "mem_arm_fifo_000002"))


def test_summary_may_not_repeat_or_include_itself() -> None:
    with pytest.raises(ValidationError, match="lists a parent more than once"):
        a_memory(
            memory_kind=MemoryKind.SUMMARY,
            parent_memory_ids=("mem_arm_fifo_000001", "mem_arm_fifo_000001"),
        )
    with pytest.raises(ValidationError, match="cannot be its own parent"):
        a_memory(
            memory_id="mem_arm_fifo_000009",
            memory_kind=MemoryKind.SUMMARY,
            parent_memory_ids=("mem_arm_fifo_000009", "mem_arm_fifo_000001"),
        )


def test_retired_memory_cannot_read_as_active() -> None:
    with pytest.raises(ValidationError, match="names no retirement cycle"):
        a_memory(status=MemoryStatus.EVICTED)
    with pytest.raises(ValidationError, match="must not name a retirement cycle"):
        a_memory(retirement_cycle=5)


def test_memory_cannot_retire_before_it_is_born() -> None:
    with pytest.raises(ValidationError, match="retires before it is born"):
        a_memory(status=MemoryStatus.EVICTED, retirement_cycle=1)


def test_pinned_memory_cannot_be_retired() -> None:
    with pytest.raises(ValidationError, match="pinned memory .* cannot be evicted"):
        a_memory(pinned=True, status=MemoryStatus.EVICTED, retirement_cycle=7)


def test_retire_refuses_a_non_retirement_status() -> None:
    with pytest.raises(ValueError, match="not a retirement status"):
        a_memory().retire(status=MemoryStatus.ACTIVE, cycle=4)


def test_retire_produces_an_inactive_record() -> None:
    retired = a_memory().retire(status=MemoryStatus.COMPRESSED, cycle=8)
    assert not retired.is_active
    assert retired.status is MemoryStatus.COMPRESSED
    assert retired.retirement_cycle == 8


def test_citation_cycle_applies_decay_and_advances_recency() -> None:
    memory = a_memory().evolve(discounted_citation_score=10.0)
    updated = memory.with_citation_cycle(cycle=5, citations=2, decay=0.9)
    assert updated.discounted_citation_score == pytest.approx(11.0)
    assert updated.citation_count == 2
    assert updated.last_verified_citation_cycle == 5


def test_uncited_cycle_decays_without_advancing_recency() -> None:
    memory = a_memory().evolve(
        discounted_citation_score=10.0, citation_count=1, last_verified_citation_cycle=3
    )
    updated = memory.with_citation_cycle(cycle=9, citations=0, decay=0.5)
    assert updated.discounted_citation_score == pytest.approx(5.0)
    assert updated.citation_count == 1
    assert updated.last_verified_citation_cycle == 3


def test_citation_bookkeeping_must_be_self_consistent() -> None:
    with pytest.raises(ValidationError, match="names a citation cycle but no citations"):
        a_memory(last_verified_citation_cycle=5)
    with pytest.raises(ValidationError, match="cited before it was born"):
        a_memory(citation_count=1, last_verified_citation_cycle=1)


def test_retention_density_is_weight_per_token() -> None:
    memory = a_memory(token_count=4).evolve(discounted_citation_score=8.0)
    assert memory.retention_density == pytest.approx(2.0)


def test_make_memory_id_is_ordered_and_rejects_negatives() -> None:
    assert make_memory_id(ArmId.ARM_LRU, 7) == "mem_arm_lru_000007"
    assert make_memory_id(ArmId.ARM_LRU, 7) < make_memory_id(ArmId.ARM_LRU, 8)
    with pytest.raises(ValueError, match="non-negative"):
        make_memory_id(ArmId.ARM_LRU, -1)


def test_lineage_edge_rejects_self_reference() -> None:
    with pytest.raises(ValidationError, match="cannot descend from itself"):
        MemoryLineageEdge(
            parent_memory_id="mem_arm_fifo_000001",
            child_memory_id="mem_arm_fifo_000001",
            relation=LineageRelation.COMPRESSED_INTO,
            cycle=2,
        )


def test_schema_version_is_pinned_to_one() -> None:
    with pytest.raises(ValidationError):
        a_memory(schema_version=2)


def test_round_trips_through_serialisation() -> None:
    memory = a_memory(citation_count=3, last_verified_citation_cycle=6)
    assert Memory.model_validate(memory.model_dump()) == memory
    assert Memory.model_validate_json(memory.model_dump_json()) == memory


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        a_memory(unexpected="value")
