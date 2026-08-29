"""Two-stage lossy summarisation: plan, commit, and the fallback when neither works."""

from __future__ import annotations

import pytest

from attention_sink.domain import (
    ArmId,
    LineageError,
    LineageRelation,
    MemoryKind,
    MemoryStatus,
    PolicyDecisionCode,
    PolicyError,
    SummarizationConfig,
    UnsatisfiableBudgetError,
)
from attention_sink.policies import SummarizationPolicy
from tests.factories import (
    CURRENT_CYCLE,
    MemorySpec,
    budget,
    build_state,
    context,
    summary_for,
    uniform_state,
)

ARM = ArmId.ARM_SUMMARY
CTX = context(ARM)


def policy(**changes: object) -> SummarizationPolicy:
    base: dict[str, object] = {"summary_target_token_limit": 8, "safety_margin_tokens": 0}
    return SummarizationPolicy(config=SummarizationConfig(**{**base, **changes}))  # type: ignore[arg-type]


def test_a_state_within_budget_plans_nothing() -> None:
    state = uniform_state(ARM, count=3, tokens=5)
    decision = policy().rebalance(state, budget(100), CTX)
    assert decision.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert decision.compression_plan is None
    assert decision.is_final


def test_stage_a_plans_the_shortest_oldest_prefix_that_reaches_the_budget() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    decision = policy().rebalance(state, budget(30), CTX)
    assert decision.decision_code is PolicyDecisionCode.COMPRESSION_PLANNED
    assert not decision.is_final
    plan = decision.compression_plan
    assert plan is not None
    # 50 active tokens against a 30-token budget: replacing sources with an 8-token
    # summary needs 30 tokens freed, so the shortest qualifying prefix is three.
    assert plan.source_memory_ids == (
        "mem_arm_summary_000000",
        "mem_arm_summary_000001",
        "mem_arm_summary_000002",
    )
    assert plan.summary_memory_id == "mem_arm_summary_000005"
    assert plan.tokens_freed == 30
    assert plan.summary_target_token_limit == 8


def test_stage_a_invents_no_summary_content_and_retires_nothing() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    decision = policy().rebalance(state, budget(30), CTX)
    assert decision.created_memories == ()
    assert decision.retired_memory_ids == ()
    assert decision.kept_memory_ids == state.active_memory_ids
    assert decision.tokens_after == decision.tokens_before


def test_a_safety_margin_forces_a_larger_compression() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    relaxed = policy(safety_margin_tokens=0).rebalance(state, budget(30), CTX)
    strict = policy(safety_margin_tokens=12).rebalance(state, budget(30), CTX)
    assert relaxed.compression_plan is not None
    assert strict.compression_plan is not None
    assert len(strict.compression_plan.source_memory_ids) > len(
        relaxed.compression_plan.source_memory_ids
    )


def test_a_plan_always_names_at_least_two_sources() -> None:
    state = uniform_state(ARM, count=4, tokens=10)
    decision = policy().rebalance(state, budget(35), CTX)
    plan = decision.compression_plan
    assert plan is not None
    assert len(plan.source_memory_ids) >= 2


def test_stage_b_commits_the_summary_and_records_lineage() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=5)
    decision = policy().finalize_compression(state, budget(30), CTX, plan, summary)

    assert decision.decision_code is PolicyDecisionCode.COMPRESSION_COMMITTED
    assert decision.is_final
    assert decision.committed_compression == plan
    assert decision.created_memories == (summary,)
    assert decision.tokens_after == 50 - 30 + 5
    assert {e.parent_memory_id for e in decision.lineage_edges} == set(plan.source_memory_ids)
    assert all(e.child_memory_id == summary.memory_id for e in decision.lineage_edges)
    assert all(e.relation is LineageRelation.COMPRESSED_INTO for e in decision.lineage_edges)


def test_applying_the_commit_marks_sources_compressed_and_keeps_them_readable() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=5)
    after = state.apply(policy().finalize_compression(state, budget(30), CTX, plan, summary))

    for source_id in plan.source_memory_ids:
        source = after.get(source_id)
        assert source is not None
        assert source.status is MemoryStatus.COMPRESSED
        assert source.retirement_cycle == CURRENT_CYCLE
    assert after.active_tokens <= 30
    assert summary.memory_id in after.active_memory_ids
    assert len(after.lineage_edges) == len(plan.source_memory_ids)


def test_the_summary_is_charged_against_the_same_budget() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=8)
    decision = policy().finalize_compression(state, budget(30), CTX, plan, summary)
    assert decision.tokens_after == 50 - 30 + 8
    assert decision.tokens_before == 50


def test_a_summary_naming_the_wrong_sources_is_rejected() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    wrong = summary_for(
        state, ("mem_arm_summary_000002", "mem_arm_summary_000003"), cycle=CURRENT_CYCLE
    )
    with pytest.raises(LineageError, match="names parents"):
        policy().finalize_compression(state, budget(30), CTX, plan, wrong)


def test_a_summary_in_the_wrong_slot_is_rejected() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    displaced = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE).evolve(
        memory_id="mem_arm_summary_000099"
    )
    with pytest.raises(LineageError, match="does not occupy the identifier"):
        policy().finalize_compression(state, budget(30), CTX, plan, displaced)


def test_a_memory_that_is_not_a_summary_is_rejected() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    plain = state.mint(
        text="not actually a summary",
        token_count=4,
        memory_kind=MemoryKind.GENERATED,
        cycle=CURRENT_CYCLE,
    )
    with pytest.raises(LineageError, match="not a summary"):
        policy().finalize_compression(state, budget(30), CTX, plan, plain)


def test_an_oversized_summary_is_a_broken_contract_not_an_infeasible_budget() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    fat = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=40)
    with pytest.raises(PolicyError, match="over the 8 the policy allowed"):
        policy().finalize_compression(state, budget(30), CTX, plan, fat)


def test_a_summary_of_already_retired_sources_is_rejected() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=5)
    once = state.apply(policy().finalize_compression(state, budget(30), CTX, plan, summary))
    with pytest.raises(LineageError, match="not active"):
        policy().finalize_compression(once, budget(30), CTX, plan, summary)


def test_a_summary_that_still_leaves_the_arm_over_budget_asks_for_another() -> None:
    state = uniform_state(ARM, count=6, tokens=10)
    engine = policy(summary_target_token_limit=9)
    plan = engine.rebalance(state, budget(25), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=9)
    decision = engine.finalize_compression(state, budget(25), CTX, plan, summary)

    if decision.decision_code is PolicyDecisionCode.COMPRESSION_PLANNED:
        assert decision.committed_compression == plan
        assert decision.compression_plan is not None
        assert decision.compression_plan != plan
        assert not decision.is_final
        assert state.apply(decision).active_memory_ids == decision.kept_memory_ids
    else:
        assert decision.is_final


def test_no_legal_compression_falls_back_to_oldest_first_eviction() -> None:
    state = uniform_state(ARM, count=4, tokens=10)
    decision = policy(summary_target_token_limit=64).rebalance(state, budget(20), CTX)
    assert decision.decision_code is PolicyDecisionCode.SUMMARY_FALLBACK_FIFO
    assert decision.is_final
    assert decision.retired_memory_ids == ("mem_arm_summary_000000", "mem_arm_summary_000001")
    assert "no legal compression" in decision.explanation


def test_too_few_eligible_sources_falls_back_as_well() -> None:
    state = build_state(
        ARM, [MemorySpec(tokens=30, cycle=1), MemorySpec(tokens=10, cycle=CURRENT_CYCLE)]
    )
    decision = policy().rebalance(state, budget(20), CTX)
    assert decision.decision_code is PolicyDecisionCode.SUMMARY_FALLBACK_FIFO
    assert decision.retired_memory_ids == ("mem_arm_summary_000000",)


def test_a_disabled_fallback_raises_instead_of_becoming_a_different_mechanism() -> None:
    state = uniform_state(ARM, count=4, tokens=10)
    engine = policy(summary_target_token_limit=64, fifo_fallback_enabled=False)
    with pytest.raises(UnsatisfiableBudgetError, match="fallback is disabled"):
        engine.rebalance(state, budget(20), CTX)


def test_a_plan_made_under_a_looser_budget_triggers_a_second_compression() -> None:
    """A replayed or forked plan is re-checked against the budget in force now."""
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=8)

    decision = policy().finalize_compression(state, budget(20), CTX, plan, summary)
    assert decision.decision_code is PolicyDecisionCode.COMPRESSION_PLANNED
    assert decision.committed_compression == plan
    assert decision.compression_plan is not None
    assert decision.compression_plan != plan
    assert not decision.is_final
    assert state.apply(decision).active_memory_ids == decision.kept_memory_ids


def test_a_tightened_budget_with_no_legal_compression_falls_back_to_eviction() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=8)

    decision = policy(summary_target_token_limit=64).finalize_compression(
        state, budget(20), CTX, plan, summary
    )
    assert decision.decision_code is PolicyDecisionCode.SUMMARY_FALLBACK_FIFO
    assert decision.is_final
    assert decision.committed_compression == plan
    assert decision.tokens_after <= 20
    after = state.apply(decision)
    assert after.active_tokens <= 20
    assert summary.memory_id in after.active_memory_ids


def test_a_tightened_budget_with_the_fallback_disabled_raises() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    plan = policy().rebalance(state, budget(30), CTX).compression_plan
    assert plan is not None
    summary = summary_for(state, plan.source_memory_ids, cycle=CURRENT_CYCLE, tokens=8)

    engine = policy(summary_target_token_limit=64, fifo_fallback_enabled=False)
    with pytest.raises(UnsatisfiableBudgetError, match="no further legal compression"):
        engine.finalize_compression(state, budget(20), CTX, plan, summary)
