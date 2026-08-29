"""The guards that only fire when something upstream is already wrong.

These paths exist so that a malformed decision or a corrupted state fails loudly
instead of quietly producing a run that looks valid. Untested, they would be exactly
the code that has never run when it is finally needed.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from attention_sink.domain import (
    ArmId,
    CandidateRank,
    CitationSource,
    CompressionPlan,
    HeuristicTokenCounter,
    LineageError,
    LineageRelation,
    MemoryKind,
    MemoryLineageEdge,
    MemoryRetirement,
    MemoryState,
    MemoryStatus,
    PolicyDecision,
    PolicyDecisionCode,
    PolicyError,
    RandomDraw,
    StateError,
    VerifiedCitation,
)
from attention_sink.policies import FifoPolicy
from attention_sink.policies.base import MEMORY_ID_FIELD, rank_candidates
from tests.factories import RUN_ID, MemorySpec, budget, build_state, context, uniform_state

ARM = ArmId.ARM_FIFO
CODE = PolicyDecisionCode.EVICTED_OLDEST


def test_an_ordering_that_is_not_total_is_refused() -> None:
    memories = uniform_state(ARM, count=2).active_memories
    with pytest.raises(ValueError, match="must end in memory_id"):
        rank_candidates(memories, (("birth_cycle", lambda m: m.birth_cycle),))
    with pytest.raises(ValueError, match="must end in memory_id"):
        rank_candidates(memories, ())
    assert len(rank_candidates(memories, (MEMORY_ID_FIELD,))) == 2


def test_the_token_counter_is_pure_and_charges_nothing_for_blank_text() -> None:
    counter = HeuristicTokenCounter()
    assert counter.count("") == 0
    assert counter.count("   \n ") == 0
    assert counter.count("a") == 1
    assert counter.count("abcdefgh") == 2
    assert counter.count("one two") == counter.count("one two")
    assert counter.count("one two") >= counter.count("one")
    assert counter.version == "heuristic-v1"


def test_a_writer_citation_of_whitespace_evidence_is_refused() -> None:
    with pytest.raises(ValidationError, match="carries no evidence"):
        VerifiedCitation(
            run_id=RUN_ID,
            arm_id=ARM,
            cycle=1,
            memory_id="mem_arm_fifo_000000",
            source=CitationSource.WRITER,
            auditor_version="auditor-v1",
            evidence="   ",
        )


def test_duplicate_memory_ids_are_refused_by_the_state() -> None:
    state = uniform_state(ARM, count=2)
    corrupted = state.model_dump()
    corrupted["memories"] = [corrupted["memories"][0], corrupted["memories"][0]]
    with pytest.raises(ValidationError, match="duplicate memory ids"):
        MemoryState.model_validate(corrupted)


def test_a_reused_creation_sequence_is_refused() -> None:
    state = uniform_state(ARM, count=2)
    corrupted = state.model_dump()
    memories = list(corrupted["memories"])
    memories[1] = {**memories[1], "creation_sequence": memories[0]["creation_sequence"]}
    corrupted["memories"] = memories
    with pytest.raises(ValidationError, match="reuse a creation sequence"):
        MemoryState.model_validate(corrupted)


def test_a_next_sequence_that_would_reuse_a_slot_is_refused() -> None:
    state = uniform_state(ARM, count=2)
    with pytest.raises(ValidationError, match="would reuse the slot"):
        MemoryState.model_validate({**state.model_dump(), "next_creation_sequence": 1})


def test_a_lineage_edge_to_an_unknown_memory_is_refused() -> None:
    state = uniform_state(ARM, count=2)
    with pytest.raises(ValidationError, match="lineage edge names unknown memories"):
        MemoryState.model_validate(
            {
                **state.model_dump(),
                "lineage_edges": [
                    MemoryLineageEdge(
                        parent_memory_id="mem_arm_fifo_000000",
                        child_memory_id="mem_arm_fifo_000099",
                        relation=LineageRelation.COMPRESSED_INTO,
                        cycle=1,
                    ).model_dump()
                ],
            }
        )


def test_admitting_below_the_next_free_slot_is_refused() -> None:
    state = uniform_state(ARM, count=2)
    stale = state.memories[1].evolve(memory_id="mem_arm_fifo_000009", creation_sequence=0)
    with pytest.raises(StateError, match="below the next free slot"):
        state.admit([stale])


def a_decision(**changes: object) -> PolicyDecision:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "arm_id": ARM,
        "cycle": 20,
        "policy_version": "fifo-v1",
        "decision_code": CODE,
        "budget_tokens": 100,
        "tokens_before": 20,
        "tokens_after": 10,
        "kept_memory_ids": ("mem_arm_fifo_000001",),
        "retired_memory_ids": ("mem_arm_fifo_000000",),
        "retirements": (
            MemoryRetirement(
                memory_id="mem_arm_fifo_000000", status=MemoryStatus.EVICTED, reason=CODE
            ),
        ),
        "candidate_order": (
            CandidateRank(memory_id="mem_arm_fifo_000000", rank_index=0, rank_key="k"),
        ),
        "explanation": "arm_fifo cycle 20: evicted one memory.",
    }
    return PolicyDecision(**{**base, **changes})  # type: ignore[arg-type]


def test_applying_a_decision_whose_kept_set_is_wrong_is_refused() -> None:
    state = uniform_state(ARM, count=3, tokens=10)
    with pytest.raises(PolicyError, match="but it keeps"):
        state.apply(a_decision())


def test_applying_a_decision_whose_projection_is_wrong_is_refused() -> None:
    state = uniform_state(ARM, count=2, tokens=10)
    with pytest.raises(PolicyError, match="but it projected"):
        state.apply(a_decision(tokens_after=5))


def test_a_final_decision_over_its_budget_cannot_be_constructed_at_all() -> None:
    """The budget guarantee lives in the decision, so no state can ever apply one."""
    with pytest.raises(ValidationError, match="over the 5-token budget"):
        a_decision(budget_tokens=5, tokens_after=10)


def test_applying_a_summary_that_names_the_wrong_parents_is_refused() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0),
            MemorySpec(tokens=10, cycle=1),
            MemorySpec(tokens=10, cycle=2),
        ],
    )
    summary = state.mint(
        text="a summary of two memories",
        token_count=4,
        memory_kind=MemoryKind.SUMMARY,
        cycle=20,
        parent_memory_ids=("mem_arm_fifo_000001", "mem_arm_fifo_000002"),
    )
    plan = CompressionPlan(
        source_memory_ids=("mem_arm_fifo_000000", "mem_arm_fifo_000001"),
        summary_memory_id=summary.memory_id,
        summary_target_token_limit=8,
        tokens_freed=20,
        safety_margin_tokens=0,
    )
    decision = a_decision(
        tokens_before=30,
        tokens_after=14,
        kept_memory_ids=("mem_arm_fifo_000002", summary.memory_id),
        retired_memory_ids=("mem_arm_fifo_000000", "mem_arm_fifo_000001"),
        retirements=(
            MemoryRetirement(
                memory_id="mem_arm_fifo_000000", status=MemoryStatus.COMPRESSED, reason=CODE
            ),
            MemoryRetirement(
                memory_id="mem_arm_fifo_000001", status=MemoryStatus.COMPRESSED, reason=CODE
            ),
        ),
        candidate_order=(
            CandidateRank(memory_id="mem_arm_fifo_000000", rank_index=0, rank_key="k0"),
            CandidateRank(memory_id="mem_arm_fifo_000001", rank_index=1, rank_key="k1"),
        ),
        created_memories=(summary,),
        committed_compression=plan,
    )
    with pytest.raises(LineageError, match="but the decision compressed"):
        state.apply(decision)


def test_a_draw_that_lists_a_candidate_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="lists a candidate more than once"):
        RandomDraw(
            decision_index=0,
            digest="a" * 64,
            candidate_memory_ids=("m1", "m1"),
            selected_index=0,
            selected_memory_id="m1",
        )


def test_a_draw_that_selects_out_of_range_is_refused() -> None:
    with pytest.raises(ValidationError, match="out of range"):
        RandomDraw(
            decision_index=0,
            digest="a" * 64,
            candidate_memory_ids=("m1",),
            selected_index=4,
            selected_memory_id="m1",
        )


def test_a_created_memory_that_is_not_kept_is_refused() -> None:
    state = uniform_state(ARM, count=2, tokens=10)
    summary = state.mint(
        text="a summary",
        token_count=4,
        memory_kind=MemoryKind.SUMMARY,
        cycle=20,
        parent_memory_ids=state.active_memory_ids,
    )
    with pytest.raises(ValidationError, match="created memories are not kept"):
        a_decision(created_memories=(summary,))


def test_a_lineage_edge_to_an_uncreated_child_is_refused() -> None:
    with pytest.raises(ValidationError, match="did not create"):
        a_decision(
            lineage_edges=(
                MemoryLineageEdge(
                    parent_memory_id="mem_arm_fifo_000000",
                    child_memory_id="mem_arm_fifo_000009",
                    relation=LineageRelation.COMPRESSED_INTO,
                    cycle=20,
                ),
            )
        )


def test_a_committed_compression_must_match_the_compressed_memories() -> None:
    plan = CompressionPlan(
        source_memory_ids=("mem_arm_fifo_000007", "mem_arm_fifo_000008"),
        summary_memory_id="mem_arm_fifo_000009",
        summary_target_token_limit=8,
        tokens_freed=20,
        safety_margin_tokens=0,
    )
    with pytest.raises(ValidationError, match="do not match the memories marked compressed"):
        a_decision(committed_compression=plan)


def test_a_decision_naming_an_unknown_memory_cannot_be_applied() -> None:
    state = uniform_state(ARM, count=2, tokens=10)
    with pytest.raises(PolicyError, match="not active"):
        state.apply(
            a_decision(
                kept_memory_ids=("mem_arm_fifo_000000", "mem_arm_fifo_000001"),
                retired_memory_ids=("mem_arm_fifo_000099",),
                retirements=(
                    MemoryRetirement(
                        memory_id="mem_arm_fifo_000099", status=MemoryStatus.EVICTED, reason=CODE
                    ),
                ),
                candidate_order=(
                    CandidateRank(memory_id="mem_arm_fifo_000099", rank_index=0, rank_key="k"),
                ),
            )
        )


def test_the_full_cycle_of_decide_and_apply_is_consistent() -> None:
    state = uniform_state(ARM, count=4, tokens=10)
    decision = FifoPolicy().rebalance(state, budget(25), context(ARM))
    after = state.apply(decision)
    assert after.active_memory_ids == decision.kept_memory_ids
    assert after.active_tokens == decision.tokens_after
