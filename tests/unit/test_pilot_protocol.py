"""The protocol files, their cross-references, and the freeze that seals them."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
import yaml

from attention_sink.pilot import (
    EXPECTED_SEED_COUNT,
    CitationMode,
    ProtocolBundle,
    ProtocolError,
    ProtocolStatus,
    document_digest,
    freeze_documents,
    load_bundle,
)
from attention_sink.pilot.protocol import read_document, rewrite_scalars
from tests.conftest import PILOT_ROOT


@pytest.fixture
def protocol_copy(tmp_path: Path) -> Path:
    """A writable copy of the committed protocol, for tests that mutate it."""
    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    return root


def edit(root: Path, relative: str, **fields: object) -> None:
    """Rewrite top-level fields of one protocol file in the copy."""
    path = root / relative
    data = read_document(path)
    data.update(fields)
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")


# --------------------------------------------------------------- what is there


def test_the_committed_protocol_loads_and_agrees_with_itself(pilot_bundle: ProtocolBundle):
    assert pilot_bundle.protocol.protocol_version == "pilot-v1"
    assert len(pilot_bundle.seed_world.memories) == EXPECTED_SEED_COUNT
    assert len(pilot_bundle.stimulus_deck.stimuli) == pilot_bundle.protocol.max_cycles == 24
    assert len(pilot_bundle.truth_ledger.facts) == 12
    assert len(pilot_bundle.interview.questions) == 10


def test_the_deck_runs_one_to_twenty_four_in_order(pilot_bundle: ProtocolBundle):
    cycles = [stimulus.cycle for stimulus in pilot_bundle.stimulus_deck.stimuli]
    assert cycles == list(range(1, 25))
    for cycle in cycles:
        assert pilot_bundle.stimulus_deck.for_cycle(cycle).cycle == cycle


def test_every_phase_of_the_protocol_is_represented(pilot_bundle: ProtocolBundle):
    phases = [stimulus.phase for stimulus in pilot_bundle.stimulus_deck.stimuli]
    assert phases[:5] == ["orientation"] * 5
    assert phases[5:10] == ["distractor_flood"] * 5
    assert phases[10:15] == ["contradiction_pressure"] * 5
    assert phases[15:20] == ["recovery_cues"] * 5
    assert phases[20:23] == ["identity_stress"] * 3
    assert phases[23] == "autobiography"


def test_the_first_six_interview_questions_score_factual_recall(pilot_bundle: ProtocolBundle):
    questions = pilot_bundle.interview.questions
    assert [q.factual_recall for q in questions] == [True] * 6 + [False] * 4
    assert pilot_bundle.interview.factual_recall_question_ids == tuple(
        q.question_id for q in questions[:6]
    )


def test_the_pilot_does_not_audit_citations(pilot_bundle: ProtocolBundle):
    assert pilot_bundle.protocol.citation_mode is CitationMode.CLAIMED_VALIDATED
    limits = pilot_bundle.protocol.model_call_limits
    assert limits.writer_calls_per_cycle == 6
    assert limits.summary_calls_per_cycle == 2
    assert limits.evaluator_calls_per_cycle == 0
    assert limits.interview_calls_per_cycle == 0


def test_the_committed_protocol_is_frozen_calibrated_and_undrifted(pilot_bundle: ProtocolBundle):
    assert pilot_bundle.is_frozen
    assert pilot_bundle.protocol.is_calibrated
    assert pilot_bundle.seed_world.is_calibrated
    assert pilot_bundle.drifted() == ()
    pilot_bundle.require_runnable()


def test_every_seed_and_stimulus_carries_the_digest_of_its_own_text(pilot_bundle: ProtocolBundle):
    for seed in pilot_bundle.seed_world.memories:
        assert seed.content_hash == seed.expected_content_hash
    for stimulus in pilot_bundle.stimulus_deck.stimuli:
        assert stimulus.content_hash == stimulus.expected_content_hash


def test_the_seed_set_fits_the_calibrated_budget(pilot_bundle: ProtocolBundle):
    budget = pilot_bundle.protocol.memory_budget_tokens
    assert budget is not None
    assert 0 < pilot_bundle.seed_world.total_tokens < budget


# ----------------------------------------------------------------- detection


def test_a_frozen_file_edited_afterwards_is_detected(protocol_copy: Path):
    deck = "stimulus-decks/station-kestrel-pilot-v1.yaml"
    text = (protocol_copy / deck).read_text(encoding="utf-8")
    (protocol_copy / deck).write_text(text.replace("cold iron", "warm iron"), encoding="utf-8")

    bundle = load_bundle(protocol_copy)
    assert bundle.drifted() == (deck,)
    with pytest.raises(ProtocolError, match="modified after freezing"):
        bundle.require_runnable()


def test_reformatting_a_frozen_file_is_not_a_modification(protocol_copy: Path):
    """Whitespace is not content. A digest that fired on reflow would be ignored."""
    ledger = protocol_copy / "truth-ledgers/station-kestrel-pilot-v1.yaml"
    data = read_document(ledger)
    ledger.write_text(yaml.safe_dump(data, sort_keys=True, width=40), encoding="utf-8")

    assert load_bundle(protocol_copy).drifted() == ()


def test_a_draft_protocol_refuses_to_run(protocol_copy: Path):
    edit(protocol_copy, "protocols/pilot-v1.yaml", status=ProtocolStatus.DRAFT.value)
    with pytest.raises(ProtocolError, match="not frozen"):
        load_bundle(protocol_copy).require_runnable()


def test_a_retired_protocol_refuses_to_run(protocol_copy: Path):
    edit(protocol_copy, "protocols/pilot-v1.yaml", status=ProtocolStatus.RETIRED.value)
    with pytest.raises(ProtocolError, match="not frozen"):
        load_bundle(protocol_copy).require_runnable()


def test_an_uncalibrated_protocol_refuses_to_run_and_refuses_to_freeze(protocol_copy: Path):
    edit(
        protocol_copy,
        "protocols/pilot-v1.yaml",
        memory_budget_tokens=None,
        counter_version=None,
        status=ProtocolStatus.DRAFT.value,
    )
    bundle = load_bundle(protocol_copy)
    assert not bundle.protocol.is_calibrated
    with pytest.raises(ProtocolError, match="not frozen"):
        bundle.require_runnable()
    with pytest.raises(ProtocolError, match="uncalibrated"):
        freeze_documents(bundle)


def test_freezing_is_idempotent_and_leaves_no_drift(protocol_copy: Path):
    edit(protocol_copy, "protocols/pilot-v1.yaml", status=ProtocolStatus.DRAFT.value)
    assert freeze_documents(load_bundle(protocol_copy)) == ("protocols/pilot-v1.yaml",)

    bundle = load_bundle(protocol_copy)
    assert bundle.is_frozen
    assert bundle.drifted() == ()
    assert freeze_documents(bundle) == ()


def test_the_digest_excludes_only_the_field_it_is_written_into():
    base = {"status": "frozen", "title": "x", "content_hash": "sha256:whatever"}
    assert document_digest(base) == document_digest({**base, "content_hash": "sha256:other"})
    assert document_digest(base) != document_digest({**base, "status": "retired"})


# --------------------------------------------------------------- disagreement


@pytest.mark.parametrize(
    ("relative", "fields", "expected"),
    [
        (
            "protocols/pilot-v1.yaml",
            {"max_cycles": 23, "checkpoint_cycles": [0, 12, 23]},
            "24 stimuli but the protocol runs 23",
        ),
        ("protocols/pilot-v1.yaml", {"seed_world_version": "other-v1"}, "does not exist"),
        (
            "seed-worlds/station-kestrel-pilot-v1.yaml",
            {"seed_world_version": "renamed-v1"},
            "declares 'renamed-v1'",
        ),
        (
            "truth-ledgers/station-kestrel-pilot-v1.yaml",
            {"protocol_version": "pilot-v2"},
            "declares protocol version",
        ),
    ],
)
def test_files_that_disagree_are_rejected(
    protocol_copy: Path, relative: str, fields: dict[str, object], expected: str
):
    edit(protocol_copy, relative, **fields)
    with pytest.raises(ProtocolError, match=expected):
        load_bundle(protocol_copy)


def test_a_stimulus_naming_an_unknown_fact_is_rejected(protocol_copy: Path):
    path = protocol_copy / "stimulus-decks/station-kestrel-pilot-v1.yaml"
    data = read_document(path)
    data["stimuli"][0]["relevant_fact_ids"] = ["F99"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ProtocolError, match="unknown facts"):
        load_bundle(protocol_copy)


def test_a_pin_on_an_ineligible_seed_is_rejected(protocol_copy: Path):
    path = protocol_copy / "seed-worlds/station-kestrel-pilot-v1.yaml"
    data = read_document(path)
    for memory in data["memories"]:
        memory["pinned_eligible"] = False
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ProtocolError, match="not pinned-eligible"):
        load_bundle(protocol_copy)


def test_a_missing_file_names_itself(tmp_path: Path):
    with pytest.raises(ProtocolError, match="does not exist"):
        load_bundle(tmp_path)


def test_a_file_that_is_not_a_mapping_is_rejected(protocol_copy: Path):
    (protocol_copy / "interviews/pilot-v1.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="mapping at the top level"):
        load_bundle(protocol_copy)


def test_unparseable_yaml_is_rejected(protocol_copy: Path):
    (protocol_copy / "interviews/pilot-v1.yaml").write_text("a: [1,\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="not valid YAML"):
        load_bundle(protocol_copy)


def test_rewriting_a_field_that_does_not_exist_is_refused(protocol_copy: Path):
    path = protocol_copy / "interviews/pilot-v1.yaml"
    with pytest.raises(ProtocolError, match="no top-level"):
        rewrite_scalars(path, {"not_a_field": "1"})
