"""Citation-weighted retention, its recency reserve, and what breaking it looks like."""

from __future__ import annotations

import pytest

from attention_sink.domain import ArmId, HeavyHitterConfig, PolicyDecisionCode
from attention_sink.policies import CitationWeightPolicy
from tests.factories import MemorySpec, budget, build_state, context

ARM = ArmId.ARM_HEAVY


def policy(reserve: int = 2) -> CitationWeightPolicy:
    return CitationWeightPolicy(config=HeavyHitterConfig(recency_reserve=reserve))


def test_evicts_the_least_densely_cited_first() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, score=0.0),
            MemorySpec(tokens=10, cycle=1, score=5.0),
            MemorySpec(tokens=10, cycle=2, score=1.0),
            MemorySpec(tokens=10, cycle=3, score=9.0),
        ],
    )
    decision = policy(reserve=0).rebalance(state, budget(20), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_LOWEST_RETENTION_DENSITY
    assert decision.retired_memory_ids == ("mem_arm_heavy_000000", "mem_arm_heavy_000002")


def test_density_prefers_a_cheap_memory_over_an_expensive_one_of_equal_weight() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=40, cycle=0, score=4.0),
            MemorySpec(tokens=4, cycle=1, score=4.0),
            MemorySpec(tokens=10, cycle=2, score=100.0),
        ],
    )
    decision = policy(reserve=0).rebalance(state, budget(20), context(ARM))
    assert decision.retired_memory_ids == ("mem_arm_heavy_000000",)


def test_the_recency_reserve_protects_the_newest_memories() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i, score=0.0) for i in range(5)])
    decision = policy(reserve=2).rebalance(state, budget(20), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_LOWEST_RETENTION_DENSITY
    assert decision.kept_memory_ids == ("mem_arm_heavy_000003", "mem_arm_heavy_000004")


def test_a_reserved_memory_outranks_a_heavily_cited_unreserved_one() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, score=99.0),
            MemorySpec(tokens=10, cycle=1, score=0.0),
            MemorySpec(tokens=10, cycle=2, score=0.0),
        ],
    )
    decision = policy(reserve=2).rebalance(state, budget(20), context(ARM))
    assert decision.retired_memory_ids == ("mem_arm_heavy_000000",)


def test_the_reserve_is_invaded_only_when_the_budget_demands_it() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i, score=float(i)) for i in range(5)])
    decision = policy(reserve=2).rebalance(state, budget(10), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.HEAVY_HITTER_RESERVE_BROKEN
    assert len(decision.retired_memory_ids) == 4
    assert decision.kept_memory_ids == ("mem_arm_heavy_000004",)
    assert "invading the recency reserve" in decision.explanation


def test_reserved_memories_sort_behind_every_unreserved_one() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i, score=0.0) for i in range(4)])
    decision = policy(reserve=2).rebalance(state, budget(10), context(ARM))
    reserved_flags = [c.rank_key.startswith("recency_reserved=1") for c in decision.candidate_order]
    assert reserved_flags == [False, False, True, True]


def test_a_zero_reserve_leaves_every_memory_eligible() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i, score=0.0) for i in range(3)])
    decision = policy(reserve=0).rebalance(state, budget(10), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_LOWEST_RETENTION_DENSITY
    assert decision.kept_memory_ids == ("mem_arm_heavy_000002",)


def test_ties_in_density_fall_through_to_score_then_recency_then_age() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=5, score=0.0),
            MemorySpec(tokens=10, cycle=1, score=0.0),
            MemorySpec(tokens=10, cycle=3, score=0.0),
        ],
    )
    decision = policy(reserve=0).rebalance(state, budget(10), context(ARM))
    assert [c.memory_id for c in decision.candidate_order] == [
        "mem_arm_heavy_000001",
        "mem_arm_heavy_000002",
        "mem_arm_heavy_000000",
    ]


def test_scores_decay_between_cycles_and_change_the_ranking() -> None:
    state = build_state(
        ARM,
        [MemorySpec(tokens=10, cycle=0, score=4.0), MemorySpec(tokens=10, cycle=1, score=3.0)],
    )
    assert policy(0).rebalance(state, budget(10), context(ARM)).retired_memory_ids == (
        "mem_arm_heavy_000001",
    )
    decayed = state
    for cycle in range(5, 9):
        decayed = decayed.record_cycle_citations([], cycle=cycle, decay=0.5)
    scores = [m.discounted_citation_score for m in decayed.active_memories]
    assert scores == pytest.approx([0.25, 0.1875])
    assert policy(0).rebalance(decayed, budget(10), context(ARM)).retired_memory_ids == (
        "mem_arm_heavy_000001",
    )
