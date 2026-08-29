"""Least recently cited: recency measured from audited writer citations only."""

from __future__ import annotations

from attention_sink.domain import ArmId, CitationSource, PolicyDecisionCode, VerifiedCitation
from attention_sink.policies import LeastRecentlyCitedPolicy
from tests.factories import RUN_ID, MemorySpec, budget, build_state, context, uniform_state

ARM = ArmId.ARM_LRU
POLICY = LeastRecentlyCitedPolicy()


def cite(memory_id: str, source: CitationSource) -> VerifiedCitation:
    return VerifiedCitation(
        run_id=RUN_ID,
        arm_id=ARM,
        cycle=9,
        memory_id=memory_id,
        source=source,
        auditor_version="auditor-v1",
        evidence="quoted verbatim in the thought",
    )


def test_never_cited_memories_are_evicted_before_cited_ones() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, citation_count=1, last_cited=8),
            MemorySpec(tokens=10, cycle=1),
            MemorySpec(tokens=10, cycle=2, citation_count=1, last_cited=3),
        ],
    )
    decision = POLICY.rebalance(state, budget(20), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_LEAST_RECENTLY_CITED
    assert decision.retired_memory_ids == ("mem_arm_lru_000001",)


def test_cited_memories_are_ordered_by_how_long_ago_they_were_used() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, citation_count=1, last_cited=9),
            MemorySpec(tokens=10, cycle=1, citation_count=1, last_cited=2),
            MemorySpec(tokens=10, cycle=2, citation_count=1, last_cited=6),
        ],
    )
    decision = POLICY.rebalance(state, budget(10), context(ARM))
    assert decision.retired_memory_ids == ("mem_arm_lru_000001", "mem_arm_lru_000002")


def test_a_writer_citation_moves_a_memory_out_of_danger() -> None:
    state = uniform_state(ARM, count=3, tokens=10)
    oldest = state.active_memory_ids[0]
    assert POLICY.rebalance(state, budget(20), context(ARM)).retired_memory_ids == (oldest,)

    refreshed = state.record_cycle_citations(
        [cite(oldest, CitationSource.WRITER)], cycle=9, decay=0.9
    )
    decision = POLICY.rebalance(refreshed, budget(20), context(ARM))
    assert oldest not in decision.retired_memory_ids
    assert decision.retired_memory_ids == ("mem_arm_lru_000001",)


def test_an_interview_citation_leaves_the_order_untouched() -> None:
    state = uniform_state(ARM, count=3, tokens=10)
    oldest = state.active_memory_ids[0]
    probed = state.record_cycle_citations(
        [cite(oldest, CitationSource.INTERVIEW), cite(oldest, CitationSource.EVALUATION)],
        cycle=9,
        decay=0.9,
    )
    assert POLICY.rebalance(probed, budget(20), context(ARM)).retired_memory_ids == (oldest,)


def test_the_rank_key_distinguishes_never_cited_from_long_ago() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0),
            MemorySpec(tokens=10, cycle=1, citation_count=1, last_cited=4),
        ],
    )
    decision = POLICY.rebalance(state, budget(10), context(ARM))
    keys = {c.memory_id: c.rank_key for c in decision.candidate_order}
    assert keys["mem_arm_lru_000000"].startswith("never_cited=0;last_verified_citation_cycle=-1")
    assert keys["mem_arm_lru_000001"].startswith("never_cited=1;last_verified_citation_cycle=4")


def test_ties_fall_through_to_birth_cycle_then_identifier() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=c) for c in (3, 1, 2)])
    decision = POLICY.rebalance(state, budget(10), context(ARM))
    assert [c.memory_id for c in decision.candidate_order] == [
        "mem_arm_lru_000001",
        "mem_arm_lru_000002",
        "mem_arm_lru_000000",
    ]
