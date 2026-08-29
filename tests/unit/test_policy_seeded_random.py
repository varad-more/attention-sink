"""Seeded random eviction: a control that has to replay exactly."""

from __future__ import annotations

import random

from attention_sink.domain import ArmId, PolicyDecision, PolicyDecisionCode, selection_digest
from attention_sink.policies import SeededRandomPolicy
from tests.factories import RUN_SEED, budget, context, uniform_state

ARM = ArmId.ARM_RANDOM
POLICY = SeededRandomPolicy()


def test_the_same_seed_reproduces_the_same_decision() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    first = POLICY.rebalance(state, budget(30), context(ARM))
    second = SeededRandomPolicy().rebalance(state, budget(30), context(ARM))
    assert first == second


def test_different_seeds_produce_different_decisions() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    a = POLICY.rebalance(state, budget(30), context(ARM, seed=RUN_SEED))
    b = POLICY.rebalance(state, budget(30), context(ARM, seed="seed-fedcba9876543210"))
    assert a.retired_memory_ids != b.retired_memory_ids


def test_different_arms_and_cycles_diverge_under_one_run_seed() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    early = POLICY.rebalance(state, budget(30), context(ARM, cycle=20))
    late = POLICY.rebalance(state, budget(30), context(ARM, cycle=21))
    assert early.retired_memory_ids != late.retired_memory_ids


def test_provenance_replays_the_selection_without_the_policy() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    decision = POLICY.rebalance(state, budget(30), context(ARM))
    provenance = decision.random_provenance
    assert provenance is not None

    for draw in provenance.draws:
        digest = selection_digest(
            run_random_seed=provenance.run_random_seed,
            arm_id=ARM.value,
            cycle=decision.cycle,
            decision_index=draw.decision_index,
            candidate_memory_ids=draw.candidate_memory_ids,
        )
        assert digest == draw.digest
        replayed = random.Random(int(digest, 16)).randrange(len(draw.candidate_memory_ids))
        assert replayed == draw.selected_index
        assert draw.candidate_memory_ids[replayed] == draw.selected_memory_id


def test_one_draw_is_recorded_per_eviction() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    decision = POLICY.rebalance(state, budget(30), context(ARM))
    assert decision.random_provenance is not None
    assert len(decision.random_provenance.draws) == len(decision.retired_memory_ids)
    assert [d.selected_memory_id for d in decision.random_provenance.draws] == list(
        decision.retired_memory_ids
    )


def test_each_draw_narrows_the_candidate_pool_by_one() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    decision = POLICY.rebalance(state, budget(30), context(ARM))
    assert decision.random_provenance is not None
    sizes = [len(d.candidate_memory_ids) for d in decision.random_provenance.draws]
    assert sizes == list(range(8, 8 - len(sizes), -1))


def test_no_provenance_is_recorded_when_nothing_is_drawn() -> None:
    state = uniform_state(ARM, count=3, tokens=5)
    decision = POLICY.rebalance(state, budget(100), context(ARM))
    assert decision.decision_code is PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
    assert decision.random_provenance is None


def test_the_decision_round_trips_with_its_provenance() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    decision = POLICY.rebalance(state, budget(30), context(ARM))
    assert PolicyDecision.model_validate_json(decision.model_dump_json()) == decision


def test_the_module_global_generator_is_never_touched() -> None:
    state = uniform_state(ARM, count=8, tokens=10)
    random.seed(1234)
    before = random.random()
    random.seed(1234)
    POLICY.rebalance(state, budget(30), context(ARM))
    assert random.random() == before
