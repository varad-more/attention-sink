"""What must never reach a writer, asserted against the bytes actually sent.

The gateway already refuses banned vocabulary on every rendered request. These tests
are about the layer above it: that the *engine* never assembles a request containing
something the guard was not written to catch -- another arm's memories, a stimulus
from a later cycle, or a line out of the truth ledger.
"""

from __future__ import annotations

import pytest

from attention_sink.domain import ArmId
from attention_sink.model_gateway import ThoughtOutput
from attention_sink.model_gateway.rendering import BANNED_POLICY_VERSIONS, parse_memory_block
from attention_sink.pilot import ProtocolBundle, build_run
from tests.conftest import fixed_clock
from tests.doubles import RecordingInvoker, recording_gateway

MECHANISM_WORDS = (
    "fifo",
    "least recently",
    "heavy hitter",
    "attention sink",
    "sliding window",
    "pinned origin",
    "eviction",
    "rebalance",
    "memory policy",
)
"""Words that would name the mechanism. Listed here rather than imported so that a
prompt which stopped being guarded would fail this test rather than trivially pass it
against an emptied constant."""


@pytest.fixture
def two_cycles(pilot_bundle: ProtocolBundle) -> RecordingInvoker:
    """Two committed cycles, and every request they sent."""
    gateway, invoker = recording_gateway()
    engine = build_run(pilot_bundle, run_id="run_blind", gateway=gateway)
    engine.clock = fixed_clock
    engine.run_cycle(1)
    engine.run_cycle(2)
    return invoker


def writer_requests(invoker: RecordingInvoker) -> list[str]:
    return [
        f"{call.system}\n{call.user}"
        for call in invoker.calls
        if call.output_model is ThoughtOutput
    ]


def test_two_cycles_send_exactly_twelve_writer_requests(two_cycles: RecordingInvoker):
    assert len(writer_requests(two_cycles)) == 12


def test_no_arm_identifier_reaches_a_writer(two_cycles: RecordingInvoker):
    for text in two_cycles.texts:
        for arm in ArmId:
            assert arm.value not in text


def test_no_real_memory_identifier_reaches_a_writer(two_cycles: RecordingInvoker):
    """A real identifier reads ``mem_arm_fifo_000003`` and would name the mechanism."""
    for text in two_cycles.texts:
        assert "mem_arm" not in text


def test_no_policy_version_or_mechanism_word_reaches_a_writer(two_cycles: RecordingInvoker):
    for text in two_cycles.texts:
        lowered = text.lower()
        for banned in (*BANNED_POLICY_VERSIONS, *MECHANISM_WORDS):
            assert banned not in lowered, banned


def test_a_writer_sees_only_its_own_arm_s_active_memories(
    pilot_bundle: ProtocolBundle,
):
    """The memory block is exactly this arm's active set, in order, and nothing else."""
    gateway, invoker = recording_gateway()
    engine = build_run(pilot_bundle, run_id="run_own", gateway=gateway)
    engine.clock = fixed_clock
    engine.run_cycle(1)

    requests = [c for c in invoker.calls if c.output_model is ThoughtOutput]
    for arm_id, call in zip(engine.configuration.arms, requests, strict=True):
        # The seed set is what every arm held going into cycle 1.
        expected = [m.text for m in engine.state_of(arm_id).memories if m.birth_cycle == 0]
        assert [text for _ref, text in parse_memory_block(call.user)] == expected


def test_no_later_stimulus_reaches_an_earlier_cycle(
    pilot_bundle: ProtocolBundle, two_cycles: RecordingInvoker
):
    deck = pilot_bundle.stimulus_deck
    first_six = writer_requests(two_cycles)[:6]
    for text in first_six:
        assert deck.for_cycle(1).text in text
        for cycle in range(2, 25):
            assert deck.for_cycle(cycle).text not in text


def test_the_truth_ledger_reaches_a_writer_only_through_the_memories_it_holds(
    pilot_bundle: ProtocolBundle, two_cycles: RecordingInvoker
):
    """A canonical statement may appear only as an arm's own memory, never as a fact.

    Phrased this way rather than as a flat "no statement appears" because a seed
    memory legitimately carries the fact it encodes, and one of the twelve is worded
    almost identically in both files. What must never happen is a statement reaching
    a writer that the arm was not shown as a memory -- that would be the ledger
    telling a writer what is true.
    """
    for call in two_cycles.calls:
        shown = " ".join(text for _ref, text in parse_memory_block(call.user))
        for statement in pilot_bundle.truth_ledger.statements:
            if statement in call.user or statement in call.system:
                assert statement in shown, statement


def test_no_seed_metadata_reaches_a_writer(
    pilot_bundle: ProtocolBundle, two_cycles: RecordingInvoker
):
    """Fact identifiers, categories, and importances are scoring apparatus."""
    for text in two_cycles.texts:
        for fact in pilot_bundle.truth_ledger.facts:
            assert fact.fact_id not in text
        for seed in pilot_bundle.seed_world.memories:
            assert seed.memory_id not in text


def test_no_evaluator_note_or_pressure_type_reaches_a_writer(
    pilot_bundle: ProtocolBundle, two_cycles: RecordingInvoker
):
    for text in two_cycles.texts:
        for stimulus in pilot_bundle.stimulus_deck.stimuli:
            assert stimulus.evaluator_notes not in text
            assert stimulus.phase not in text
