"""The protocol files, their cross-references, and the validation that seals them."""

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
    load_bundle,
    promote_documents,
    return_to_draft,
)
from attention_sink.pilot.protocol import (
    DOCUMENT_PATHS,
    EXACT_TOKEN_COUNT_SOURCES,
    read_document,
    rewrite_scalars,
)
from tests.conftest import LOCAL_COUNTER_SOURCE, PILOT_ROOT


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
    assert len(pilot_bundle.stimulus_deck.stimuli) == pilot_bundle.protocol.maximum_cycles == 24
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


def test_the_committed_protocol_is_validated_calibrated_and_undrifted(
    pilot_bundle: ProtocolBundle,
):
    assert pilot_bundle.is_local_validated
    assert pilot_bundle.protocol.is_calibrated
    assert pilot_bundle.seed_world.is_calibrated
    assert pilot_bundle.drifted() == ()
    pilot_bundle.require_runnable()


def test_the_committed_protocol_is_frozen_against_an_exact_counter(
    pilot_bundle: ProtocolBundle,
):
    """Phase 8 froze it, and only because the budget stopped being an approximation.

    The local-first override allowed freezing at exactly one point: after the budget
    had been re-derived against the model that will read the memories. Everything
    asserted here is a precondition of that, so a protocol that lost any of them
    would fail this test rather than quietly launch a canonical run.
    """
    protocol = pilot_bundle.protocol
    assert pilot_bundle.is_frozen
    assert protocol.status is ProtocolStatus.FROZEN
    assert protocol.token_count_source in EXACT_TOKEN_COUNT_SOURCES
    assert protocol.token_count_source != LOCAL_COUNTER_SOURCE
    assert protocol.is_exactly_calibrated
    assert protocol.calibration_writer_model_id
    assert protocol.calibration_region
    assert protocol.calibrated_at is not None
    assert set(protocol.calibration_input_hashes) == {"seed_memories.yaml", "stimuli.yaml"}
    pilot_bundle.require_runnable(canonical=True)


def test_a_local_run_of_the_frozen_protocol_is_still_a_local_run(pilot_bundle: ProtocolBundle):
    """FROZEN runs locally. What it must not do is claim the canonical counter."""
    assert pilot_bundle.protocol.status.runs_locally
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


def test_a_validated_file_edited_afterwards_is_detected(protocol_copy: Path):
    deck = "stimuli.yaml"
    text = (protocol_copy / deck).read_text(encoding="utf-8")
    (protocol_copy / deck).write_text(text.replace("cold iron", "warm iron"), encoding="utf-8")

    bundle = load_bundle(protocol_copy)
    assert bundle.drifted() == (deck,)
    with pytest.raises(ProtocolError, match="modified after validation"):
        bundle.require_runnable()


def test_reformatting_a_validated_file_is_not_a_modification(protocol_copy: Path):
    """Whitespace is not content. A digest that fired on reflow would be ignored."""
    ledger = protocol_copy / "truth_ledger.yaml"
    data = read_document(ledger)
    ledger.write_text(yaml.safe_dump(data, sort_keys=True, width=40), encoding="utf-8")

    assert load_bundle(protocol_copy).drifted() == ()


def test_a_draft_protocol_refuses_to_run(protocol_copy: Path):
    edit(protocol_copy, "protocol.yaml", status=ProtocolStatus.DRAFT.value)
    with pytest.raises(ProtocolError, match="not validated"):
        load_bundle(protocol_copy).require_runnable()


def test_a_retired_protocol_refuses_to_run(protocol_copy: Path):
    edit(protocol_copy, "protocol.yaml", status=ProtocolStatus.RETIRED.value)
    with pytest.raises(ProtocolError, match="not validated"):
        load_bundle(protocol_copy).require_runnable()


def test_an_uncalibrated_protocol_refuses_to_run_and_refuses_to_validate(protocol_copy: Path):
    edit(
        protocol_copy,
        "protocol.yaml",
        memory_budget_tokens=None,
        counter_version=None,
        status=ProtocolStatus.DRAFT.value,
    )
    bundle = load_bundle(protocol_copy)
    assert not bundle.protocol.is_calibrated
    with pytest.raises(ProtocolError, match="not validated"):
        bundle.require_runnable()
    with pytest.raises(ProtocolError, match="uncalibrated"):
        promote_documents(bundle)


def test_validation_is_idempotent_and_leaves_no_drift(protocol_copy: Path):
    # Every file, because the committed protocol is frozen and local validation is a
    # step below that: promoting to it rewrites all five, not only the edited one.
    return_to_draft(load_bundle(protocol_copy))
    assert set(promote_documents(load_bundle(protocol_copy))) == set(DOCUMENT_PATHS)

    bundle = load_bundle(protocol_copy)
    assert bundle.is_local_validated
    assert bundle.drifted() == ()
    assert promote_documents(bundle) == ()


@pytest.mark.parametrize("status", [ProtocolStatus.DRAFT, ProtocolStatus.RETIRED])
def test_no_command_may_promote_a_protocol_to_a_status_outside_the_ladder(
    protocol_copy: Path, status: ProtocolStatus
):
    """Draft is reached by returning to it, and retirement is not a promotion."""
    with pytest.raises(ProtocolError, match="not a status"):
        promote_documents(load_bundle(protocol_copy), status)


def test_returning_to_draft_clears_every_digest(protocol_copy: Path):
    assert set(return_to_draft(load_bundle(protocol_copy))) == set(DOCUMENT_PATHS)
    bundle = load_bundle(protocol_copy)
    assert not bundle.is_local_validated
    assert all(document.content_hash == "" for _, document in bundle.named_documents)
    assert return_to_draft(bundle) == ()


def test_the_digest_excludes_only_the_field_it_is_written_into():
    base = {"status": "local_validated", "title": "x", "content_hash": "sha256:whatever"}
    assert document_digest(base) == document_digest({**base, "content_hash": "sha256:other"})
    assert document_digest(base) != document_digest({**base, "status": "retired"})


# --------------------------------------------------------------- disagreement


@pytest.mark.parametrize(
    ("relative", "fields", "expected"),
    [
        (
            "protocol.yaml",
            {"maximum_cycles": 23, "checkpoint_cycles": [0, 12, 23]},
            "24 stimuli but the protocol runs 23",
        ),
        (
            "protocol.yaml",
            {"seed_world_version": "other-v1"},
            "names seed world 'other-v1'",
        ),
        (
            "protocol.yaml",
            {"protocol_version": "pilot-v9"},
            "not the requested 'pilot-v1'",
        ),
        (
            "seed_memories.yaml",
            {"seed_world_version": "renamed-v1"},
            "declares 'renamed-v1'",
        ),
        (
            "truth_ledger.yaml",
            {"protocol_version": "pilot-v2"},
            "declares protocol version",
        ),
        (
            "protocol.yaml",
            {"checkpoint_cycles": [0, 12]},
            "is not a checkpoint",
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
    path = protocol_copy / "stimuli.yaml"
    data = read_document(path)
    data["stimuli"][0]["relevant_fact_ids"] = ["F99"]
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    with pytest.raises(ProtocolError, match="unknown facts"):
        load_bundle(protocol_copy)


def test_a_pin_on_an_ineligible_seed_is_rejected(protocol_copy: Path):
    path = protocol_copy / "seed_memories.yaml"
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
    (protocol_copy / "interview_questions.yaml").write_text("- a\n- b\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="mapping at the top level"):
        load_bundle(protocol_copy)


def test_unparseable_yaml_is_rejected(protocol_copy: Path):
    (protocol_copy / "interview_questions.yaml").write_text("a: [1,\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="not valid YAML"):
        load_bundle(protocol_copy)


def test_rewriting_a_field_that_does_not_exist_is_refused(protocol_copy: Path):
    path = protocol_copy / "interview_questions.yaml"
    with pytest.raises(ProtocolError, match="no top-level"):
        rewrite_scalars(path, {"not_a_field": "1"})
