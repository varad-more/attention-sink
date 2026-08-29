"""Pinned origin: one memory that no budget pressure can remove."""

from __future__ import annotations

import pytest

from attention_sink.domain import (
    ArmId,
    PinnedOriginConfig,
    PolicyDecisionCode,
    UnsatisfiableBudgetError,
)
from attention_sink.policies import PinnedOriginPolicy
from tests.factories import MemorySpec, budget, build_state, context

ARM = ArmId.ARM_SINK
PINNED = "mem_arm_sink_000000"


def test_a_flagged_memory_is_never_evicted() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, pinned=True),
            *[MemorySpec(tokens=10, cycle=i) for i in range(1, 4)],
        ],
    )
    decision = PinnedOriginPolicy().rebalance(state, budget(10), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.EVICTED_OUTSIDE_WINDOW
    assert decision.kept_memory_ids == (PINNED,)
    assert PINNED not in decision.retired_memory_ids


def test_a_configured_memory_is_never_evicted_even_without_the_flag() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i) for i in range(4)])
    policy = PinnedOriginPolicy(config=PinnedOriginConfig(pinned_memory_id=PINNED))
    decision = policy.rebalance(state, budget(20), context(ARM))
    assert PINNED in decision.kept_memory_ids
    assert decision.retired_memory_ids == ("mem_arm_sink_000001", "mem_arm_sink_000002")


def test_the_pinned_memory_is_absent_from_the_candidate_ordering() -> None:
    state = build_state(ARM, [MemorySpec(tokens=10, cycle=i) for i in range(3)])
    policy = PinnedOriginPolicy(config=PinnedOriginConfig(pinned_memory_id=PINNED))
    decision = policy.rebalance(state, budget(20), context(ARM))
    assert PINNED not in [c.memory_id for c in decision.candidate_order]


def test_the_window_slides_oldest_first_over_everything_else() -> None:
    state = build_state(
        ARM,
        [
            MemorySpec(tokens=10, cycle=0, pinned=True),
            *[MemorySpec(tokens=10, cycle=i) for i in range(1, 5)],
        ],
    )
    decision = PinnedOriginPolicy().rebalance(state, budget(30), context(ARM))
    assert decision.retired_memory_ids == ("mem_arm_sink_000001", "mem_arm_sink_000002")
    assert decision.kept_memory_ids == (PINNED, "mem_arm_sink_000003", "mem_arm_sink_000004")


def test_a_pinned_memory_larger_than_the_budget_is_unsatisfiable() -> None:
    state = build_state(
        ARM, [MemorySpec(tokens=50, cycle=0, pinned=True), MemorySpec(tokens=10, cycle=1)]
    )
    with pytest.raises(UnsatisfiableBudgetError) as raised:
        PinnedOriginPolicy().rebalance(state, budget(20), context(ARM))
    assert raised.value.context == {
        "run_id": "run_test",
        "arm_id": "arm_sink",
        "cycle": 20,
        "policy_version": "sink-v1",
    }
