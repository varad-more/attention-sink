"""Invariants every mechanism must hold over generated states, not chosen ones.

The unit suites assert what each arm does on cases a person thought of. These assert
what no arm may ever do, over hundreds of states per property. A mechanism that only
behaves on the examples its author imagined is not a mechanism that can be trusted to
produce a defensible experimental result.
"""

from __future__ import annotations

import contextlib
import random

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from attention_sink.domain import (
    CANONICAL_ARMS,
    ArmId,
    MemoryState,
    PolicyDecision,
    PolicyDecisionCode,
    TokenBudget,
    UnsatisfiableBudgetError,
    selection_digest,
)
from attention_sink.policies import SeededRandomPolicy, policy_for
from tests.factories import (
    CURRENT_CYCLE,
    MemorySpec,
    budget,
    build_state,
    context,
    memory_specs,
    resolve,
    states_and_budgets,
)

ARMS = pytest.mark.parametrize("arm", CANONICAL_ARMS, ids=[a.value for a in CANONICAL_ARMS])

GENERATED = settings(
    max_examples=250,
    deadline=None,
    suppress_health_check=[HealthCheck.too_slow],
)
"""Enough examples that a rare ordering collision is found rather than assumed away.

The deadline is disabled because every transition revalidates a Pydantic model, which
is deliberate -- the cost buys the guarantee that no unvalidated record can exist -- and
makes per-example timing a poor signal.
"""


@ARMS
@GENERATED
@given(data=st.data())
def test_a_final_decision_never_exceeds_the_budget(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, working = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    assert decision.is_final
    assert decision.tokens_after <= limit.max_active_tokens
    assert working.apply(decision).active_tokens <= limit.max_active_tokens


@ARMS
@GENERATED
@given(data=st.data())
def test_the_input_state_is_never_mutated(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    before = state.model_dump_json()
    with contextlib.suppress(UnsatisfiableBudgetError):
        resolve(policy_for(arm), state, limit, context(arm))
    assert state.model_dump_json() == before


@ARMS
@GENERATED
@given(data=st.data())
def test_a_pinned_memory_is_never_retired(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    pinned = {m.memory_id for m in state.active_memories if m.pinned}
    try:
        decision, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    assert not pinned & set(decision.retired_memory_ids)
    assert pinned <= set(decision.kept_memory_ids)


@ARMS
@GENERATED
@given(data=st.data())
def test_every_run_either_reaches_the_budget_or_raises_a_typed_error(
    arm: ArmId, data: st.DataObject
) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError as error:
        assert error.arm_id == arm.value
        assert error.run_id is not None
        assert error.cycle is not None
        assert error.policy_version is not None
        return
    assert limit.is_satisfied_by(decision.tokens_after)


@ARMS
@GENERATED
@given(data=st.data())
def test_the_same_inputs_always_produce_the_same_decision(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        first, _ = resolve(policy_for(arm), state, limit, context(arm))
        second, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    assert first == second


@ARMS
@GENERATED
@given(data=st.data())
def test_the_recorded_ordering_is_total(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    keys = [c.rank_key for c in decision.candidate_order]
    assert len(set(keys)) == len(keys)
    assert all(key.split(";")[-1].startswith("memory_id=") for key in keys)


@ARMS
@GENERATED
@given(data=st.data())
def test_retirements_are_a_prefix_of_the_recorded_ordering(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    ranked = [c.memory_id for c in decision.candidate_order]
    assert list(decision.retired_memory_ids) == ranked[: len(decision.retired_memory_ids)]


@ARMS
@GENERATED
@given(data=st.data())
def test_applying_a_decision_yields_exactly_what_it_kept(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, working = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    after = working.apply(decision)
    assert after.active_memory_ids == decision.kept_memory_ids
    assert after.active_tokens == decision.tokens_after
    assert len(after.memories) >= len(state.memories)


@ARMS
@GENERATED
@given(data=st.data())
def test_a_decision_round_trips_through_json(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, _ = resolve(policy_for(arm), state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    assert PolicyDecision.model_validate_json(decision.model_dump_json()) == decision


@ARMS
@GENERATED
@given(data=st.data())
def test_a_state_round_trips_through_json(arm: ArmId, data: st.DataObject) -> None:
    state, _ = data.draw(states_and_budgets(arm))
    assert MemoryState.model_validate_json(state.model_dump_json()) == state


@ARMS
@settings(max_examples=120, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(specs=memory_specs(min_size=1, max_size=8), size=st.integers(min_value=1, max_value=60))
def test_repeated_rebalancing_stays_legal(arm: ArmId, specs: list[MemorySpec], size: int) -> None:
    state = build_state(arm, specs)
    limit = budget(size)
    for cycle in range(CURRENT_CYCLE, CURRENT_CYCLE + 3):
        state = state.admit(
            [
                state.mint(
                    text=f"a thought from cycle {cycle}",
                    token_count=3,
                    memory_kind=state.memories[0].memory_kind,
                    cycle=cycle,
                )
            ]
        )
        try:
            decision, working = resolve(policy_for(arm), state, limit, context(arm, cycle=cycle))
        except UnsatisfiableBudgetError:
            return
        state = working.apply(decision)
        assert state.active_tokens <= limit.max_active_tokens


@ARMS
@GENERATED
@given(data=st.data())
def test_a_settled_state_needs_no_further_action(arm: ArmId, data: st.DataObject) -> None:
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision, working = resolve(policy_for(arm), state, limit, context(arm))
        settled = working.apply(decision)
        again, _ = resolve(policy_for(arm), settled, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    assert again.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert again.retired_memory_ids == ()


@GENERATED
@given(data=st.data())
def test_random_draws_replay_from_their_recorded_provenance(data: st.DataObject) -> None:
    arm = ArmId.ARM_RANDOM
    state, limit = data.draw(states_and_budgets(arm))
    try:
        decision = SeededRandomPolicy().rebalance(state, limit, context(arm))
    except UnsatisfiableBudgetError:
        return
    provenance = decision.random_provenance
    if provenance is None:
        assert decision.retired_memory_ids == ()
        return
    for draw in provenance.draws:
        digest = selection_digest(
            run_random_seed=provenance.run_random_seed,
            arm_id=arm.value,
            cycle=decision.cycle,
            decision_index=draw.decision_index,
            candidate_memory_ids=draw.candidate_memory_ids,
        )
        assert digest == draw.digest
        assert random.Random(int(digest, 16)).randrange(len(draw.candidate_memory_ids)) == (
            draw.selected_index
        )


@GENERATED
@given(data=st.data(), seed=st.text(alphabet="abcdef0123456789", min_size=8, max_size=32))
def test_a_different_seed_can_change_what_the_random_arm_forgets(
    data: st.DataObject, seed: str
) -> None:
    arm = ArmId.ARM_RANDOM
    state, limit = data.draw(states_and_budgets(arm))
    try:
        a = SeededRandomPolicy().rebalance(state, limit, context(arm))
        b = SeededRandomPolicy().rebalance(state, limit, context(arm, seed=seed))
    except UnsatisfiableBudgetError:
        return
    assert (
        len(a.retired_memory_ids) == len(b.retired_memory_ids) or a.tokens_after != b.tokens_after
    )


@GENERATED
@given(
    tokens=st.lists(st.integers(min_value=1, max_value=30), min_size=1, max_size=10),
    size=st.integers(min_value=1, max_value=200),
)
def test_the_budget_comparison_is_integer_arithmetic(tokens: list[int], size: int) -> None:
    limit = TokenBudget(max_active_tokens=size, counter_version="heuristic-v1")
    total = sum(tokens)
    assert limit.is_satisfied_by(total) == (total <= size)
    assert isinstance(total, int)
