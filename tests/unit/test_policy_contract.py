"""The contract every canonical arm honours, asserted the same way for all six.

If any of these differ between arms, the arms differ in more than their mechanism
and the experiment no longer isolates the thing it claims to isolate.
"""

from __future__ import annotations

import pytest

from attention_sink.domain import (
    CANONICAL_ARMS,
    ArmId,
    MemoryState,
    PolicyDecision,
    PolicyDecisionCode,
    UnsatisfiableBudgetError,
)
from attention_sink.policies import DEFAULT_POLICIES, policy_for
from tests.factories import (
    CURRENT_CYCLE,
    MemorySpec,
    budget,
    build_state,
    context,
    resolve,
    uniform_state,
)

ARMS = pytest.mark.parametrize("arm", CANONICAL_ARMS, ids=[a.value for a in CANONICAL_ARMS])


@ARMS
def test_an_empty_state_is_handled(arm: ArmId) -> None:
    state = MemoryState(run_id="run_test", arm_id=arm)
    decision, _ = resolve(policy_for(arm), state, budget(50), context(arm))
    assert decision.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert decision.kept_memory_ids == ()
    assert decision.tokens_after == 0


@ARMS
def test_a_single_memory_within_budget_survives(arm: ArmId) -> None:
    state = uniform_state(arm, count=1, tokens=5)
    decision, working = resolve(policy_for(arm), state, budget(50), context(arm))
    assert decision.kept_memory_ids == state.active_memory_ids
    assert working.apply(decision) == state


@ARMS
def test_a_single_memory_from_an_earlier_cycle_can_be_evicted(arm: ArmId) -> None:
    state = uniform_state(arm, count=1, tokens=50)
    decision, working = resolve(policy_for(arm), state, budget(10), context(arm))
    assert decision.retired_memory_ids == state.active_memory_ids
    assert working.apply(decision).active_memory_ids == ()


@ARMS
def test_the_budget_is_never_exceeded_by_a_final_decision(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    limit = budget(35)
    decision, working = resolve(policy_for(arm), state, limit, context(arm))
    assert decision.tokens_after <= limit.max_active_tokens
    assert working.apply(decision).active_tokens <= limit.max_active_tokens


@ARMS
def test_the_input_state_is_not_mutated(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    before = state.model_dump_json()
    resolve(policy_for(arm), state, budget(35), context(arm))
    assert state.model_dump_json() == before


@ARMS
def test_a_pinned_memory_is_never_retired(arm: ArmId) -> None:
    state = build_state(
        arm,
        [
            MemorySpec(tokens=10, cycle=0, pinned=True),
            *[MemorySpec(tokens=10, cycle=i) for i in range(1, 6)],
        ],
    )
    decision, _ = resolve(policy_for(arm), state, budget(25), context(arm))
    assert f"mem_{arm.value}_000000" not in decision.retired_memory_ids
    assert state.active_memory_ids[0] in decision.kept_memory_ids


@ARMS
def test_the_current_cycle_admission_is_never_retired(arm: ArmId) -> None:
    state = build_state(
        arm,
        [
            *[MemorySpec(tokens=10, cycle=i) for i in range(4)],
            MemorySpec(tokens=10, cycle=CURRENT_CYCLE),
        ],
    )
    decision, _ = resolve(policy_for(arm), state, budget(15), context(arm))
    assert state.active_memory_ids[-1] in decision.kept_memory_ids


@ARMS
def test_a_budget_smaller_than_the_protected_memories_is_unsatisfiable(arm: ArmId) -> None:
    state = build_state(
        arm, [MemorySpec(tokens=10, cycle=1), MemorySpec(tokens=60, cycle=CURRENT_CYCLE)]
    )
    with pytest.raises(UnsatisfiableBudgetError) as raised:
        resolve(policy_for(arm), state, budget(20), context(arm))
    assert set(raised.value.context) == {"run_id", "arm_id", "cycle", "policy_version"}
    assert all(value is not None for value in raised.value.context.values())
    assert raised.value.arm_id == arm.value
    assert raised.value.cycle == CURRENT_CYCLE


@ARMS
def test_kept_and_retired_partition_the_active_set(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    decision, _ = resolve(policy_for(arm), state, budget(35), context(arm))
    created = {m.memory_id for m in decision.created_memories}
    assert set(decision.kept_memory_ids) - created | set(decision.retired_memory_ids) == set(
        state.active_memory_ids
    )
    assert not set(decision.kept_memory_ids) & set(decision.retired_memory_ids)


@ARMS
def test_every_retired_memory_appears_in_the_recorded_ordering(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    decision, _ = resolve(policy_for(arm), state, budget(35), context(arm))
    ranked = [c.memory_id for c in decision.candidate_order]
    assert set(decision.retired_memory_ids) <= set(ranked)
    assert [c.rank_index for c in decision.candidate_order] == list(range(len(ranked)))


@ARMS
def test_the_decision_round_trips_through_json(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    decision, _ = resolve(policy_for(arm), state, budget(35), context(arm))
    assert PolicyDecision.model_validate_json(decision.model_dump_json()) == decision


@ARMS
def test_the_decision_is_explained_deterministically(arm: ArmId) -> None:
    state = uniform_state(arm, count=8, tokens=10)
    first, _ = resolve(policy_for(arm), state, budget(35), context(arm))
    second, _ = resolve(policy_for(arm), state, budget(35), context(arm))
    assert first.explanation == second.explanation
    assert first.explanation.startswith(f"{arm.value} cycle {CURRENT_CYCLE}: ")
    assert first.explanation.endswith(".")


@ARMS
def test_rebalancing_repeatedly_stays_within_budget(arm: ArmId) -> None:
    state = uniform_state(arm, count=6, tokens=10)
    limit = budget(35)
    for cycle in range(CURRENT_CYCLE, CURRENT_CYCLE + 4):
        ctx = context(arm, cycle=cycle)
        state = state.admit(
            [
                state.mint(
                    text=f"a new thought in cycle {cycle}",
                    token_count=9,
                    memory_kind=state.memories[0].memory_kind,
                    cycle=cycle,
                )
            ]
        )
        decision, working = resolve(policy_for(arm), state, limit, ctx)
        state = working.apply(decision)
        assert state.active_tokens <= limit.max_active_tokens


@ARMS
def test_a_second_rebalance_of_a_settled_state_changes_nothing(arm: ArmId) -> None:
    state = uniform_state(arm, count=6, tokens=10)
    limit = budget(35)
    decision, working = resolve(policy_for(arm), state, limit, context(arm))
    settled = working.apply(decision)
    again, working_again = resolve(policy_for(arm), settled, limit, context(arm))
    assert again.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert working_again.apply(again) == settled


def test_every_canonical_arm_has_exactly_one_registered_mechanism() -> None:
    for arm in CANONICAL_ARMS:
        assert DEFAULT_POLICIES[arm].arm_id is arm
    versions = {DEFAULT_POLICIES[arm].policy_version for arm in CANONICAL_ARMS}
    assert len(versions) == len(CANONICAL_ARMS)
