"""The two reference arms that bound the result rather than competing in it."""

from __future__ import annotations

import pytest

from attention_sink.domain import ArmId, PolicyDecisionCode, UnsatisfiableBudgetError
from attention_sink.policies import FullMemoryPolicy, StatelessPolicy
from tests.factories import CURRENT_CYCLE, MemorySpec, budget, build_state, context, uniform_state


def test_full_memory_keeps_everything() -> None:
    state = uniform_state(ArmId.ARM_FULL, count=6, tokens=10)
    decision = FullMemoryPolicy().rebalance(state, budget(1000), context(ArmId.ARM_FULL))
    assert decision.decision_code is PolicyDecisionCode.RETAINED_ALL
    assert decision.kept_memory_ids == state.active_memory_ids
    assert decision.retired_memory_ids == ()
    assert state.apply(decision) == state


def test_full_memory_refuses_to_forget_when_its_budget_is_too_small() -> None:
    state = uniform_state(ArmId.ARM_FULL, count=6, tokens=10)
    with pytest.raises(UnsatisfiableBudgetError):
        FullMemoryPolicy().rebalance(state, budget(20), context(ArmId.ARM_FULL))


def test_stateless_keeps_only_this_cycle() -> None:
    state = build_state(
        ArmId.ARM_STATELESS,
        [
            MemorySpec(tokens=10, cycle=0),
            MemorySpec(tokens=10, cycle=1),
            MemorySpec(tokens=7, cycle=CURRENT_CYCLE),
        ],
    )
    decision = StatelessPolicy().rebalance(state, budget(50), context(ArmId.ARM_STATELESS))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_STATELESS
    assert decision.kept_memory_ids == ("mem_arm_stateless_000002",)
    assert decision.tokens_after == 7


def test_stateless_drops_the_past_even_when_the_budget_allows_it() -> None:
    state = uniform_state(ArmId.ARM_STATELESS, count=3, tokens=1)
    decision = StatelessPolicy().rebalance(state, budget(1000), context(ArmId.ARM_STATELESS))
    assert len(decision.retired_memory_ids) == 3
    assert decision.kept_memory_ids == ()


def test_stateless_with_nothing_carried_in_reports_no_action() -> None:
    state = build_state(ArmId.ARM_STATELESS, [MemorySpec(tokens=5, cycle=CURRENT_CYCLE)])
    decision = StatelessPolicy().rebalance(state, budget(50), context(ArmId.ARM_STATELESS))
    assert decision.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
