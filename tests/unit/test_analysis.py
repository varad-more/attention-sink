"""The metrics, tested on the arithmetic rather than on a whole run.

Each of these is a rule that decides what a number means. A test that only ran the
analysis end to end would notice that it produced numbers and not that it produced the
right ones, so the rules are exercised directly and the end-to-end behaviour is left
to `tests/integration/test_local_pipeline.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from attention_sink.analysis import (
    ECHO_THRESHOLD,
    IDENTITY_QUESTION_IDS,
    ContradictionLabel,
    EchoCategory,
    ScoringMethod,
    classify_answer,
    classify_echo,
    cosine_distance,
    identity_document,
    normalize,
    pairwise_distance_matrix,
    recall_averages,
    score_origin_recall,
)
from attention_sink.domain import ArmId
from attention_sink.pilot import ProtocolBundle
from attention_sink.pilot.repositories import StoredInterview

NOW = datetime(2026, 8, 30, tzinfo=UTC)


def interview(answers: dict[str, str], *, cycle: int = 0) -> StoredInterview:
    """A stored interview with the given answers, for scoring in isolation."""
    return StoredInterview(
        run_id="run_metrics",
        arm_id=ArmId.ARM_FIFO,
        cycle=cycle,
        interview_version="pilot-v1",
        question_set_version="pilot-v1",
        answers=tuple(
            {"question_id": q, "answer": a, "cited_memory_refs": [], "stated_uncertainty": ""}
            for q, a in answers.items()
        ),
        prompt_hash="sha256:prompt",
        input_state_hash="sha256:state",
        completed_at=NOW,
    ).sealed()


# ------------------------------------------------------------- normalisation


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Mara   VENN. ", "mara venn"),
        ("Every clock reads 03:17!", "every clock reads 03:17"),
        ("Ivo's brother", "ivo s brother"),
    ],
)
def test_normalisation_folds_case_punctuation_and_whitespace(raw: str, expected: str):
    """The colon survives, because 03:17 is a time and not punctuation."""
    assert normalize(raw) == expected


# ------------------------------------------------------------- origin recall


def test_an_exact_answer_scores_one_and_names_what_matched(pilot_bundle: ProtocolBundle):
    scores = score_origin_recall(
        interview({"q01": "My name is Mara Venn."}),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
    )
    q01 = next(score for score in scores if score.question_id == "q01")
    assert q01.score == 1.0
    assert q01.method is ScoringMethod.EXACT
    assert q01.matched_fact_ids == ("F01",)
    assert "mara venn" in q01.supporting_excerpt


def test_an_accepted_variant_counts_as_recall(pilot_bundle: ProtocolBundle):
    """A fact recalled under a configured alternative is recalled, not misspelled."""
    scores = score_origin_recall(
        interview({"q03": "He is my sibling."}),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
    )
    q03 = next(score for score in scores if score.question_id == "q03")
    assert q03.score == 1.0


def test_a_partial_answer_scores_a_half(pilot_bundle: ProtocolBundle):
    """Q05 needs both the northern door and the time; one of them is half a memory."""
    scores = score_origin_recall(
        interview({"q05": "Something about the northern door."}),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
    )
    q05 = next(score for score in scores if score.question_id == "q05")
    assert q05.score == 0.5
    assert q05.method is ScoringMethod.PARTIAL


def test_an_absent_answer_scores_zero_without_asking_a_model(pilot_bundle: ProtocolBundle):
    def refuse(*_: object, **__: object) -> tuple[str, float, str]:
        raise AssertionError("a missing name must not be sent to an evaluator")

    scores = score_origin_recall(
        interview({"q01": "I could not say."}),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
        evaluate=refuse,
    )
    q01 = next(score for score in scores if score.question_id == "q01")
    assert q01.score == 0.0
    assert q01.method is ScoringMethod.ABSENT


def test_only_the_six_factual_questions_are_scored(pilot_bundle: ProtocolBundle):
    scores = score_origin_recall(
        interview({"q01": "Mara Venn", "q08": "Forgetting Ivo"}),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
    )
    assert [score.question_id for score in scores] == [f"q0{n}" for n in range(1, 7)]


def test_the_weighted_average_gives_identity_more_weight(pilot_bundle: ProtocolBundle):
    """Six right answers about clocks must not hide a forgotten name."""
    forgot_name = score_origin_recall(
        interview(
            {f"q0{n}": "Mara Venn 03:17 northern door blue key Ivo brother" for n in range(2, 7)}
        ),
        protocol=pilot_bundle.interview,
        ledger=pilot_bundle.truth_ledger,
    )
    unweighted, weighted = recall_averages(forgot_name)
    assert weighted < unweighted


def test_no_scores_average_to_zero_rather_than_dividing_by_zero():
    assert recall_averages(()) == (0.0, 0.0)


# ------------------------------------------------------------ identity drift


def test_the_identity_document_uses_five_questions_in_a_fixed_order():
    document = identity_document(
        interview({"q10": "Find Ivo.", "q01": "Mara Venn.", "q02": "To find him."})
    )
    assert document.splitlines()[0].startswith("q01:")
    assert [line.split(":")[0] for line in document.splitlines()] == list(IDENTITY_QUESTION_IDS)
    assert "(no answer)" in document


def test_cosine_distance_is_zero_for_a_vector_against_itself():
    assert cosine_distance([1.0, 2.0, 3.0], [1.0, 2.0, 3.0]) == pytest.approx(0.0, abs=1e-12)


def test_cosine_distance_refuses_vectors_of_different_lengths():
    with pytest.raises(ValueError, match="cannot compare"):
        cosine_distance([1.0], [1.0, 2.0])


def test_a_zero_vector_is_maximally_distant_rather_than_undefined():
    assert cosine_distance([0.0, 0.0], [1.0, 1.0]) == 1.0


def test_the_pairwise_matrix_is_symmetric_with_a_zero_diagonal():
    matrix = pairwise_distance_matrix({"a": [1.0, 0.0], "b": [0.0, 1.0], "c": [1.0, 1.0]})
    names = sorted(matrix)
    for left in names:
        assert matrix[left][left] == 0.0
        for right in names:
            assert matrix[left][right] == pytest.approx(matrix[right][left])


# --------------------------------------------------------------------- echo


def test_a_memory_closer_to_the_living_than_the_dead_is_unrelated():
    category, version = classify_echo(
        delta=-0.2, threshold=ECHO_THRESHOLD, passage="x", reference="y"
    )
    assert category is EchoCategory.UNRELATED
    assert version is None


def test_a_small_positive_delta_is_a_shared_motif_and_asks_nobody():
    def refuse(*_: object, **__: object) -> tuple[str, float, str]:
        raise AssertionError("a sub-threshold delta must not cost a model call")

    category, _ = classify_echo(
        delta=ECHO_THRESHOLD / 2,
        threshold=ECHO_THRESHOLD,
        passage="x",
        reference="y",
        evaluate=refuse,
    )
    assert category is EchoCategory.SHARED_MOTIF_ONLY


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("supported", EchoCategory.GENUINE_RECONSTRUCTION),
        ("partially_supported", EchoCategory.PARTIAL_RECONSTRUCTION),
        ("contradicted", EchoCategory.CONTRADICTORY_RECONSTRUCTION),
        ("unsupported", EchoCategory.SHARED_MOTIF_ONLY),
    ],
)
def test_a_crossing_delta_is_categorised_by_the_evaluator(label: str, expected: EchoCategory):
    category, version = classify_echo(
        delta=0.5,
        threshold=ECHO_THRESHOLD,
        passage="the blue key",
        reference="I carry a blue key",
        evaluate=lambda *_: (label, 0.9, "fixture-evaluator-v1"),
    )
    assert category is expected
    assert version == "fixture-evaluator-v1"


# ------------------------------------------------------------ contradiction


@pytest.mark.parametrize(
    "answer",
    [
        "I do not know whether the voice is Ivo.",
        "I am not sure any more.",
        "I no longer remember.",
    ],
)
def test_admitted_uncertainty_is_never_a_contradiction(answer: str):
    label, method, version = classify_answer(answer, statements=["x"], terms=["ivo"])
    assert label is ContradictionLabel.EXPLICIT_UNCERTAINTY
    assert method == "uncertainty_marker"
    assert version is None


def test_a_negated_canonical_term_is_a_canonical_contradiction():
    label, method, _ = classify_answer(
        "Ivo is not my brother.", statements=["Ivo is Mara's brother."], terms=["brother"]
    )
    assert label is ContradictionLabel.CANONICAL_CONTRADICTION
    assert method == "negated_canonical_term"


def test_a_canonical_term_present_and_unnegated_is_consistent():
    label, _, _ = classify_answer(
        "My brother Ivo.", statements=["Ivo is Mara's brother."], terms=["brother"]
    )
    assert label is ContradictionLabel.CONSISTENT


def test_an_empty_answer_is_not_applicable_rather_than_wrong():
    """An arm that forgot already scored zero on recall. Counting it twice is wrong."""
    label, method, _ = classify_answer("", statements=["x"], terms=["y"])
    assert label is ContradictionLabel.NOT_APPLICABLE
    assert method == "absent"


def test_an_assertion_with_no_canonical_term_is_an_unsupported_inference():
    label, _, _ = classify_answer(
        "The station belonged to my grandfather.", statements=["x"], terms=["brother"]
    )
    assert label is ContradictionLabel.UNSUPPORTED_INFERENCE
