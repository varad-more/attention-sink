"""What the protocol and snapshot models refuse to be.

Built by mutating the committed protocol's own dumps rather than by hand-writing
minimal fixtures, so each case differs from something real in exactly one way and the
assertion is about that one way.
"""

from __future__ import annotations

from typing import Any

import pytest

from attention_sink.domain import ArmId
from attention_sink.pilot import (
    ArmCycleSnapshot,
    InterviewProtocol,
    PilotProtocol,
    ProtocolBundle,
    ProtocolError,
    RunSnapshot,
    SeedWorld,
    StimulusDeck,
    TruthLedger,
)


def dumped(model: Any, **overrides: Any) -> dict[str, Any]:
    """The model's own payload, with one thing changed."""
    return {**model.model_dump(), **overrides}


# ------------------------------------------------------------------ seed world


def test_a_seed_world_cannot_list_a_memory_twice(pilot_bundle: ProtocolBundle):
    world = pilot_bundle.seed_world
    memories = list(world.model_dump()["memories"])
    memories[1] = {**memories[1], "memory_id": memories[0]["memory_id"]}
    with pytest.raises(ValueError, match="more than once"):
        SeedWorld.model_validate(dumped(world, memories=memories))


def test_seed_positions_must_run_one_to_n_in_order(pilot_bundle: ProtocolBundle):
    world = pilot_bundle.seed_world
    memories = list(world.model_dump()["memories"])
    memories[0] = {**memories[0], "initial_position": 99}
    with pytest.raises(ValueError, match="initial_position must run"):
        SeedWorld.model_validate(dumped(world, memories=memories))


def test_only_one_seed_may_be_pinned_eligible(pilot_bundle: ProtocolBundle):
    world = pilot_bundle.seed_world
    memories = [{**m, "pinned_eligible": True} for m in world.model_dump()["memories"]]
    with pytest.raises(ValueError, match="at most one seed"):
        SeedWorld.model_validate(dumped(world, memories=memories))


def test_an_uncounted_seed_world_reports_itself_uncalibrated(pilot_bundle: ProtocolBundle):
    world = pilot_bundle.seed_world
    memories = list(world.model_dump()["memories"])
    memories[0] = {**memories[0], "provisional_token_count": None}
    partial = SeedWorld.model_validate(dumped(world, memories=memories))
    assert not partial.is_calibrated
    with pytest.raises(ProtocolError, match="has not been calibrated"):
        assert partial.total_tokens


# --------------------------------------------------------------- stimulus deck


def test_stimulus_cycles_must_run_one_to_n_in_order(pilot_bundle: ProtocolBundle):
    deck = pilot_bundle.stimulus_deck
    stimuli = list(deck.model_dump()["stimuli"])
    stimuli[0] = {**stimuli[0], "cycle": 7}
    with pytest.raises(ValueError, match="cycles must run"):
        StimulusDeck.model_validate(dumped(deck, stimuli=stimuli))


def test_a_deck_cannot_list_an_identifier_twice(pilot_bundle: ProtocolBundle):
    deck = pilot_bundle.stimulus_deck
    stimuli = list(deck.model_dump()["stimuli"])
    stimuli[1] = {**stimuli[1], "stimulus_id": stimuli[0]["stimulus_id"], "cycle": 2}
    with pytest.raises(ValueError, match="more than once"):
        StimulusDeck.model_validate(dumped(deck, stimuli=stimuli))


def test_asking_for_a_cycle_the_deck_does_not_have(pilot_bundle: ProtocolBundle):
    with pytest.raises(ProtocolError, match="has no cycle 99"):
        pilot_bundle.stimulus_deck.for_cycle(99)


# ---------------------------------------------------------- ledger and interview


def test_a_ledger_cannot_state_a_fact_twice(pilot_bundle: ProtocolBundle):
    ledger = pilot_bundle.truth_ledger
    facts = list(ledger.model_dump()["facts"])
    facts[1] = {**facts[1], "fact_id": facts[0]["fact_id"]}
    with pytest.raises(ValueError, match="more than once"):
        TruthLedger.model_validate(dumped(ledger, facts=facts))


def test_an_interview_cannot_ask_a_question_twice(pilot_bundle: ProtocolBundle):
    interview = pilot_bundle.interview
    questions = list(interview.model_dump()["questions"])
    questions[1] = {**questions[1], "question_id": questions[0]["question_id"]}
    with pytest.raises(ValueError, match="more than once"):
        InterviewProtocol.model_validate(dumped(interview, questions=questions))


def test_an_interview_that_scores_nothing_is_refused(pilot_bundle: ProtocolBundle):
    interview = pilot_bundle.interview
    questions = [{**q, "factual_recall": False} for q in interview.model_dump()["questions"]]
    with pytest.raises(ValueError, match="scores nothing"):
        InterviewProtocol.model_validate(dumped(interview, questions=questions))


# ------------------------------------------------------------------- protocol


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"arms": [ArmId.ARM_FIFO, ArmId.ARM_FIFO]}, "same arm twice"),
        ({"checkpoint_cycles": [0, 12, 99]}, "outside 0..24"),
        ({"checkpoint_cycles": [0, -1]}, "outside 0..24"),
        ({"checkpoint_cycles": [12, 0, 24]}, "must be ascending"),
        ({"counter_version": None}, "must be set together"),
        ({"memory_budget_tokens": None}, "must be set together"),
    ],
)
def test_a_protocol_that_contradicts_itself_is_refused(
    pilot_bundle: ProtocolBundle, overrides: dict[str, Any], expected: str
):
    with pytest.raises(ValueError, match=expected):
        PilotProtocol.model_validate(dumped(pilot_bundle.protocol, **overrides))


def test_a_pin_on_a_seed_that_does_not_exist_is_reported(pilot_bundle: ProtocolBundle):
    from attention_sink.pilot.protocol import _reference_problems

    policies = pilot_bundle.protocol.policies
    pinned = policies.pinned_origin.model_copy(update={"pinned_seed_memory_id": "seed_99"})
    protocol = pilot_bundle.protocol.model_copy(
        update={"policies": policies.model_copy(update={"pinned_origin": pinned})}
    )
    problems = _reference_problems(pilot_bundle.model_copy(update={"protocol": protocol}))
    assert any("unknown seed seed_99" in problem for problem in problems)


# ------------------------------------------------------------------ snapshots


@pytest.fixture(scope="module")
def one_cycle(pilot_bundle: ProtocolBundle) -> ArmCycleSnapshot:
    from attention_sink.model_gateway import GatewaySettings, build_gateway
    from attention_sink.pilot import build_run

    engine = build_run(
        pilot_bundle, run_id="run_snap", gateway=build_gateway(GatewaySettings.from_env(env={}))
    )
    return engine.run_cycle(1)[0]


def test_a_cycle_ending_on_an_unfinished_decision_is_refused(one_cycle: ArmCycleSnapshot):
    decision = one_cycle.policy_decisions[-1].model_dump()
    unfinished = {
        **decision,
        "compression_plan": {
            "source_memory_ids": list(one_cycle.active_memory_ids_after[:2]),
            "summary_memory_id": "mem_arm_fifo_000999",
            "summary_target_token_limit": 24,
            "tokens_freed": 6,
            "safety_margin_tokens": 0,
        },
    }
    payload = {**one_cycle.model_dump(), "policy_decisions": [unfinished]}
    with pytest.raises(ValueError, match="still awaits a summary"):
        ArmCycleSnapshot.model_validate(payload)


def test_a_summary_whose_parents_disagree_with_the_record_is_refused(
    pilot_bundle: ProtocolBundle,
):
    from attention_sink.model_gateway import GatewaySettings, build_gateway
    from attention_sink.pilot import build_run
    from attention_sink.pilot.engine import PilotEngine

    config = build_run(
        pilot_bundle, run_id="run_sum", gateway=build_gateway(GatewaySettings.from_env(env={}))
    ).configuration.model_copy(update={"memory_budget_tokens": 160})
    gateway = build_gateway(GatewaySettings.from_env(env={}))
    engine = PilotEngine(configuration=config, bundle=pilot_bundle, gateway=gateway)
    engine.initialize_pilot_run()
    snapshot = next(s for s in engine.run_cycle(1) if s.arm_id is ArmId.ARM_SUMMARY)
    assert snapshot.created_summary is not None

    payload = {**snapshot.model_dump(), "summary_source_memory_ids": ("mem_arm_summary_000000",)}
    with pytest.raises(ValueError, match="names parents"):
        ArmCycleSnapshot.model_validate(payload)

    payload = {**snapshot.model_dump(), "compressed_memory_ids": ()}
    with pytest.raises(ValueError, match="descends from"):
        ArmCycleSnapshot.model_validate(payload)


def test_a_run_past_its_own_length_is_refused(pilot_engine: Any):
    snapshot = pilot_engine.run_snapshot()
    payload = {**snapshot.model_dump(), "current_cycle": 99}
    with pytest.raises(ValueError, match="past the configured"):
        RunSnapshot.model_validate(payload)
