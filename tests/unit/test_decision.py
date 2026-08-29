"""The decision record refuses to describe something that could not have happened."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from attention_sink.domain import (
    ArmId,
    CandidateRank,
    CompressionPlan,
    MemoryRetirement,
    MemoryStatus,
    PolicyDecision,
    PolicyDecisionCode,
    RandomDraw,
    RandomProvenance,
)
from tests.factories import RUN_ID

CODE = PolicyDecisionCode.EVICTED_OLDEST


def a_decision(**changes: object) -> PolicyDecision:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "arm_id": ArmId.ARM_FIFO,
        "cycle": 4,
        "policy_version": "fifo-v1",
        "decision_code": CODE,
        "budget_tokens": 100,
        "tokens_before": 120,
        "tokens_after": 100,
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
        "explanation": "arm_fifo cycle 4: evicted one memory.",
    }
    return PolicyDecision(**{**base, **changes})  # type: ignore[arg-type]


def test_a_well_formed_decision_validates() -> None:
    assert a_decision().is_final


def test_retired_ids_must_match_the_recorded_retirements() -> None:
    with pytest.raises(ValidationError, match="does not match the recorded retirements"):
        a_decision(retired_memory_ids=("mem_arm_fifo_000002",))


def test_a_memory_cannot_be_both_kept_and_retired() -> None:
    with pytest.raises(ValidationError, match="both kept and retired"):
        a_decision(kept_memory_ids=("mem_arm_fifo_000000",))


def test_duplicate_identifiers_are_refused() -> None:
    with pytest.raises(ValidationError, match="kept memory ids contain a duplicate"):
        a_decision(kept_memory_ids=("mem_arm_fifo_000001", "mem_arm_fifo_000001"))
    retirement = MemoryRetirement(
        memory_id="mem_arm_fifo_000000", status=MemoryStatus.EVICTED, reason=CODE
    )
    with pytest.raises(ValidationError, match="retired memory ids contain a duplicate"):
        a_decision(
            retired_memory_ids=("mem_arm_fifo_000000", "mem_arm_fifo_000000"),
            retirements=(retirement, retirement),
        )


def test_a_retired_memory_must_appear_in_the_ordering() -> None:
    with pytest.raises(ValidationError, match="absent from the recorded ordering"):
        a_decision(candidate_order=())


def test_the_ordering_cannot_list_a_memory_twice() -> None:
    with pytest.raises(ValidationError, match="lists a memory more than once"):
        a_decision(
            candidate_order=(
                CandidateRank(memory_id="mem_arm_fifo_000000", rank_index=0, rank_key="k0"),
                CandidateRank(memory_id="mem_arm_fifo_000000", rank_index=1, rank_key="k1"),
            )
        )


def test_rank_indices_must_run_in_order() -> None:
    with pytest.raises(ValidationError, match="rank indices must run"):
        a_decision(
            candidate_order=(
                CandidateRank(memory_id="mem_arm_fifo_000000", rank_index=3, rank_key="k"),
            )
        )


def test_a_final_decision_must_be_within_budget() -> None:
    with pytest.raises(ValidationError, match="over the 100-token budget"):
        a_decision(tokens_after=180)


def test_a_decision_awaiting_a_summary_may_be_over_budget() -> None:
    plan = CompressionPlan(
        source_memory_ids=("mem_arm_fifo_000000", "mem_arm_fifo_000001"),
        summary_memory_id="mem_arm_fifo_000009",
        summary_target_token_limit=8,
        tokens_freed=20,
        safety_margin_tokens=0,
    )
    decision = a_decision(
        tokens_after=180,
        compression_plan=plan,
        decision_code=PolicyDecisionCode.COMPRESSION_PLANNED,
    )
    assert not decision.is_final


def test_a_retirement_status_must_actually_be_a_retirement() -> None:
    with pytest.raises(ValidationError, match="not a retirement status"):
        MemoryRetirement(memory_id="mem_arm_fifo_000000", status=MemoryStatus.ACTIVE, reason=CODE)


def test_random_provenance_needs_one_draw_per_retirement() -> None:
    provenance = RandomProvenance(
        run_random_seed="seed-0123456789abcdef",
        draws=(
            RandomDraw(
                decision_index=0,
                digest="a" * 64,
                candidate_memory_ids=("m1", "m2"),
                selected_index=0,
                selected_memory_id="m1",
            ),
            RandomDraw(
                decision_index=1,
                digest="b" * 64,
                candidate_memory_ids=("m2",),
                selected_index=0,
                selected_memory_id="m2",
            ),
        ),
    )
    with pytest.raises(ValidationError, match="one draw per retirement"):
        a_decision(random_provenance=provenance)


def test_a_draw_must_name_the_memory_its_index_points_at() -> None:
    with pytest.raises(ValidationError, match="index 1 holds"):
        RandomDraw(
            decision_index=0,
            digest="a" * 64,
            candidate_memory_ids=("m1", "m2"),
            selected_index=1,
            selected_memory_id="m1",
        )


def test_a_draw_must_record_candidates_in_the_digest_sort_order() -> None:
    with pytest.raises(ValidationError, match="not in the digest's sort order"):
        RandomDraw(
            decision_index=0,
            digest="a" * 64,
            candidate_memory_ids=("m2", "m1"),
            selected_index=0,
            selected_memory_id="m2",
        )


def test_draws_must_be_indexed_from_zero_without_gaps() -> None:
    with pytest.raises(ValidationError, match="indexed 0..n-1"):
        RandomProvenance(
            run_random_seed="seed-0123456789abcdef",
            draws=(
                RandomDraw(
                    decision_index=3,
                    digest="a" * 64,
                    candidate_memory_ids=("m1",),
                    selected_index=0,
                    selected_memory_id="m1",
                ),
            ),
        )


def test_a_compression_plan_needs_two_distinct_sources_that_exclude_the_summary() -> None:
    with pytest.raises(ValidationError):
        CompressionPlan(
            source_memory_ids=("m1",),
            summary_memory_id="m9",
            summary_target_token_limit=8,
            tokens_freed=10,
            safety_margin_tokens=0,
        )
    with pytest.raises(ValidationError, match="must be distinct"):
        CompressionPlan(
            source_memory_ids=("m1", "m1"),
            summary_memory_id="m9",
            summary_target_token_limit=8,
            tokens_freed=10,
            safety_margin_tokens=0,
        )
    with pytest.raises(ValidationError, match="cannot compress itself"):
        CompressionPlan(
            source_memory_ids=("m1", "m9"),
            summary_memory_id="m9",
            summary_target_token_limit=8,
            tokens_freed=10,
            safety_margin_tokens=0,
        )


def test_schema_version_is_pinned_to_one() -> None:
    with pytest.raises(ValidationError):
        a_decision(schema_version=2)


def test_a_decision_round_trips() -> None:
    decision = a_decision()
    assert PolicyDecision.model_validate(decision.model_dump()) == decision
