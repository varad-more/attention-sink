"""First-in, first-out: age is the only criterion, and the order must be total."""

from __future__ import annotations

from attention_sink.domain import ArmId, PolicyDecisionCode
from attention_sink.policies import FifoPolicy
from tests.factories import CURRENT_CYCLE, MemorySpec, budget, build_state, context, uniform_state

ARM = ArmId.ARM_FIFO
POLICY = FifoPolicy()


def test_evicts_the_oldest_first() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    decision = POLICY.rebalance(state, budget(30), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_OLDEST
    assert decision.retired_memory_ids == ("mem_arm_fifo_000000", "mem_arm_fifo_000001")
    assert decision.tokens_after == 30


def test_evicts_only_as_many_as_the_budget_demands() -> None:
    state = uniform_state(ARM, count=5, tokens=10)
    assert len(POLICY.rebalance(state, budget(45), context(ARM)).retired_memory_ids) == 1
    assert len(POLICY.rebalance(state, budget(25), context(ARM)).retired_memory_ids) == 3


def test_memories_born_in_one_cycle_break_ties_on_creation_order() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=4) for _ in range(4)])
    decision = POLICY.rebalance(state, budget(20), context(ARM))
    assert decision.retired_memory_ids == ("mem_arm_fifo_000000", "mem_arm_fifo_000001")
    assert [c.rank_key for c in decision.candidate_order] == [
        f"birth_cycle=4;creation_sequence={i};memory_id=mem_arm_fifo_{i:06d}" for i in range(4)
    ]


def test_identical_token_counts_do_not_disturb_the_order() -> None:
    state = uniform_state(ARM, count=6, tokens=7)
    first = POLICY.rebalance(state, budget(21), context(ARM))
    second = POLICY.rebalance(state, budget(21), context(ARM))
    assert first == second
    assert first.retired_memory_ids == tuple(f"mem_arm_fifo_{i:06d}" for i in range(3))


def test_the_current_cycle_admission_is_never_evicted() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=1),
            MemorySpec(tokens=10, cycle=2),
            MemorySpec(tokens=10, cycle=CURRENT_CYCLE),
        ],
    )
    decision = POLICY.rebalance(state, budget(10), context(ARM))
    assert decision.kept_memory_ids == ("mem_arm_fifo_000002",)
    assert "mem_arm_fifo_000002" not in decision.retired_memory_ids


def test_a_state_already_within_budget_says_so() -> None:
    state = uniform_state(ARM, count=3, tokens=5)
    decision = POLICY.rebalance(state, budget(100), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert decision.retired_memory_ids == ()
    assert decision.kept_memory_ids == state.active_memory_ids
