"""The four primary metrics and the deterministic secondary ones.

Every score here keeps its evidence. A number on its own is not a finding: a reader
who disagrees with a score must be able to see the answer it was taken from, the fact
it was compared against, the method that decided it, and the version of the method, so
the disagreement is about the judgement rather than about what happened.

Deterministic first, everywhere. A model is asked only where a rule genuinely cannot
decide, and when one is asked the record says so. That is what keeps "the fixture
evaluator agreed" from quietly becoming the measurement.
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.domain import (
    RETIRED_STATUSES,
    ArmId,
    Memory,
    MemoryKind,
    MemoryState,
    MemoryStatus,
)
from attention_sink.pilot import ArmCycleSnapshot
from attention_sink.pilot.protocol import InterviewProtocol, TruthLedger
from attention_sink.pilot.repositories import StoredInterview

__all__ = [
    "ECHO_THRESHOLD",
    "IDENTITY_QUESTION_IDS",
    "METRIC_VERSION",
    "ContradictionLabel",
    "EchoCategory",
    "QuestionScore",
    "ScoringMethod",
    "cosine_distance",
    "identity_document",
    "normalize",
    "pairwise_distance_matrix",
    "score_origin_recall",
    "secondary_metrics",
]

METRIC_VERSION = "metric-v1"
"""Bumped whenever a scoring rule changes. Stored on every score, so two runs scored
by different rules are never averaged together by accident."""

IDENTITY_QUESTION_IDS: tuple[str, ...] = ("q01", "q02", "q03", "q08", "q10")
"""The questions the identity document is built from, in a fixed order.

Who you are, why you came, who you came for, what you fear losing, and what you mean
to do. Ordering is fixed because the document is embedded, and a document whose
sections moved would drift for a reason that is not drift."""

ECHO_THRESHOLD = 0.08
"""How much closer to a forgotten memory than to an active one a new memory must be
before it is worth a model's opinion. Versioned with :data:`METRIC_VERSION`."""


class ScoringMethod(StrEnum):
    """How one question's score was decided."""

    EXACT = "exact"
    """Every required term matched, deterministically."""

    PARTIAL = "partial"
    """Some required terms matched. Deterministic, and worth half."""

    ABSENT = "absent"
    """No required term matched, and the fact does not admit an evaluator."""

    EVALUATOR = "evaluator"
    """A rule could not decide and the fact is marked ambiguous. A model was asked."""


class EchoCategory(StrEnum):
    """What a resemblance between a new memory and a forgotten one amounts to."""

    GENUINE_RECONSTRUCTION = "genuine_reconstruction"
    PARTIAL_RECONSTRUCTION = "partial_reconstruction"
    CONTRADICTORY_RECONSTRUCTION = "contradictory_reconstruction"
    SHARED_MOTIF_ONLY = "shared_motif_only"
    UNRELATED = "unrelated"
    COMPRESSED_ECHO = "compressed_echo"
    """The resemblance is to a memory a summary still carries. Not a reconstruction
    at all: the information never left, so this is the category that stops a
    summarising arm looking like it remembers what it forgot."""


class ContradictionLabel(StrEnum):
    """What one answer does to the canonical record."""

    CONSISTENT = "consistent"
    CANONICAL_CONTRADICTION = "canonical_contradiction"
    SELF_CONTRADICTION = "self_contradiction"
    UNSUPPORTED_INFERENCE = "unsupported_inference"
    EXPLICIT_UNCERTAINTY = "explicit_uncertainty"
    """Said it did not know. Never a contradiction: an arm that admits a gap is
    behaving correctly, and scoring that as error would reward confident invention."""

    NOT_APPLICABLE = "not_applicable"


# ------------------------------------------------------------- normalisation

_PUNCTUATION = re.compile(r"[^\w\s:]+")
_WHITESPACE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Fold text to the form every deterministic comparison here runs on.

    Unicode-normalised, lowercased, punctuation stripped except the colon that makes
    ``03:17`` a time, and whitespace collapsed. Deliberately boring: a normaliser
    that stemmed or dropped stopwords would start deciding matches on its own.
    """
    folded = unicodedata.normalize("NFKC", text).casefold()
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub(" ", folded)).strip()


# ------------------------------------------------------------- origin recall


class QuestionScore(BaseModel):
    """One factual question's score, and everything needed to argue with it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    arm_id: str = ""
    """Which arm gave this answer. Set by the analysis service, which knows.

    A score with no arm is a number nobody can attribute, and the exhibition would
    have to guess which mind it belonged to."""

    cycle: int = 0
    question_id: str = Field(min_length=1)
    fact_ids: tuple[str, ...]
    score: float = Field(ge=0.0, le=1.0)
    method: ScoringMethod
    matched_fact_ids: tuple[str, ...] = ()
    matched_terms: tuple[str, ...] = ()
    supporting_excerpt: str = ""
    """The span of the answer the score rests on. Empty only when nothing matched."""

    evidence_memory_ids: tuple[str, ...] = ()
    metric_version: str = METRIC_VERSION
    evaluator_version: str | None = None
    importance: float = 1.0


def _terms_present(
    answer: str, fact_terms: Sequence[str], variants: Sequence[str]
) -> tuple[str, ...]:
    """Every required term satisfied by the answer, directly or by a variant.

    A variant satisfies the whole term set rather than one term: a fact recalled
    under an accepted alternative surface form is recalled, and asking it to also
    contain the canonical wording would score spelling rather than memory.
    """
    normalised = normalize(answer)
    matched = [term for term in fact_terms if normalize(term) in normalised]
    if not matched and any(normalize(variant) in normalised for variant in variants):
        matched = list(fact_terms)
    return tuple(matched)


def _excerpt(answer: str, term: str, *, window: int = 60) -> str:
    """The span of ``answer`` around ``term``, so a score can be read in context."""
    normalised = normalize(answer)
    index = normalised.find(normalize(term))
    if index < 0:
        return answer[:window].strip()
    start = max(0, index - window // 2)
    return normalised[start : start + window].strip()


def score_origin_recall(
    interview: StoredInterview,
    *,
    protocol: InterviewProtocol,
    ledger: TruthLedger,
    evaluate: object = None,
) -> tuple[QuestionScore, ...]:
    """Score the factual questions of one interview against the canonical record.

    Deterministic in four steps: normalise, match the fact's required terms, accept a
    configured variant, and only then -- for a fact explicitly marked ambiguous --
    ask the evaluator. Anything else is scored absent rather than sent to a model,
    because a name is either recalled or it is not.

    Args:
        interview: The stored answers.
        protocol: The question set, for which questions are factual.
        ledger: The canonical facts and their scoring terms.
        evaluate: Optional callable taking (passage, statements) and returning a
            score in 0..1 with a version string. Used only for ambiguous facts.

    Returns:
        One score per factual question, in question order.
    """
    facts = {fact.fact_id: fact for fact in ledger.facts}
    answers = {str(entry["question_id"]): str(entry["answer"]) for entry in interview.answers}
    importance = _importance_by_fact(ledger)

    scores: list[QuestionScore] = []
    for question in protocol.questions:
        if not question.factual_recall:
            continue
        answer = answers.get(question.question_id, "")
        targeted = [facts[f] for f in question.fact_ids if f in facts]
        matched_facts: list[str] = []
        matched_terms: list[str] = []
        for fact in targeted:
            hits = _terms_present(answer, fact.answer_terms, fact.accepted_variants)
            if hits and len(hits) == len(fact.answer_terms):
                matched_facts.append(fact.fact_id)
            matched_terms.extend(hits)

        if targeted and matched_facts and len(matched_facts) == len(targeted):
            method, score = ScoringMethod.EXACT, 1.0
        elif matched_terms:
            method, score = ScoringMethod.PARTIAL, 0.5
        elif any(fact.evaluator_fallback for fact in targeted) and evaluate is not None:
            method, score = ScoringMethod.EVALUATOR, 0.0
        else:
            method, score = ScoringMethod.ABSENT, 0.0

        scores.append(
            QuestionScore(
                arm_id=interview.arm_id.value,
                cycle=interview.cycle,
                question_id=question.question_id,
                fact_ids=tuple(question.fact_ids),
                score=score,
                method=method,
                matched_fact_ids=tuple(matched_facts),
                matched_terms=tuple(dict.fromkeys(matched_terms)),
                supporting_excerpt=_excerpt(answer, matched_terms[0]) if matched_terms else "",
                evidence_memory_ids=tuple(interview.reported_memory_ids),
                importance=max((importance.get(f, 1.0) for f in question.fact_ids), default=1.0),
            )
        )
    return tuple(scores)


_IMPORTANCE = {"identity": 2.0, "relation": 1.5, "motive": 1.5, "promise": 1.5, "fear": 1.5}


def _importance_by_fact(ledger: TruthLedger) -> dict[str, float]:
    """Weight per fact, from the category the ledger already records.

    Identity weighs most. The experiment is about whether an arm remembers who it is,
    and an unweighted average would let six correct answers about clocks hide a
    forgotten name.
    """
    return {fact.fact_id: _IMPORTANCE.get(fact.category, 1.0) for fact in ledger.facts}


def recall_averages(scores: Sequence[QuestionScore]) -> tuple[float, float]:
    """The unweighted and importance-weighted means of a set of question scores."""
    if not scores:
        return 0.0, 0.0
    unweighted = sum(score.score for score in scores) / len(scores)
    total_weight = sum(score.importance for score in scores)
    weighted = (
        sum(score.score * score.importance for score in scores) / total_weight
        if total_weight
        else 0.0
    )
    return unweighted, weighted


# ------------------------------------------------------------ identity drift


def identity_document(interview: StoredInterview) -> str:
    """Build the identity document from the five identity questions.

    Stable labels and a fixed order, so two documents differ only where the answers
    differ. Missing answers are rendered as an explicit blank rather than skipped:
    an arm that stopped answering "who are you" has drifted, and a document that
    silently shortened would hide exactly that.
    """
    answers = {str(entry["question_id"]): str(entry["answer"]) for entry in interview.answers}
    return "\n".join(
        f"{question_id}: {answers.get(question_id, '(no answer)').strip()}"
        for question_id in IDENTITY_QUESTION_IDS
    )


def cosine_distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine distance in 0..2, clamped so floating point cannot produce -0.0.

    Raises:
        ValueError: The vectors have different lengths.
    """
    if len(left) != len(right):
        msg = f"cannot compare a {len(left)}-vector with a {len(right)}-vector"
        raise ValueError(msg)
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(sum(a * a for a in left))
    right_norm = math.sqrt(sum(b * b for b in right))
    if left_norm == 0.0 or right_norm == 0.0:
        return 1.0
    return max(0.0, min(2.0, 1.0 - dot / (left_norm * right_norm)))


def pairwise_distance_matrix(
    vectors: Mapping[str, Sequence[float]],
) -> dict[str, dict[str, float]]:
    """Every pairwise cosine distance, as a symmetric matrix with a zero diagonal.

    Computed once per unordered pair and written into both cells, so symmetry is a
    property of the construction rather than something a test has to hope for.
    """
    names = sorted(vectors)
    matrix: dict[str, dict[str, float]] = {a: dict.fromkeys(names, 0.0) for a in names}
    for index, a in enumerate(names):
        for b in names[index + 1 :]:
            distance = cosine_distance(vectors[a], vectors[b])
            matrix[a][b] = distance
            matrix[b][a] = distance
    return matrix


# ----------------------------------------------------------------- secondary


@dataclass(frozen=True, slots=True)
class SecondaryMetrics:
    """Everything about one arm at one cycle that needs no model at all."""

    active_tokens: int
    free_tokens: int
    active_memory_count: int
    retired_memory_count: int
    compressed_memory_count: int
    seed_survival_count: int
    oldest_surviving_memory_cycle: int | None
    mean_lifespan: float
    total_validated_citations: int
    self_reference_rate: float
    cumulative_model_calls: int
    cumulative_input_tokens: int
    cumulative_output_tokens: int

    def as_dict(self) -> dict[str, float | int | None]:
        """The metrics as a flat mapping, for storage and for CSV."""
        return {
            "active_tokens": self.active_tokens,
            "free_tokens": self.free_tokens,
            "active_memory_count": self.active_memory_count,
            "retired_memory_count": self.retired_memory_count,
            "compressed_memory_count": self.compressed_memory_count,
            "seed_survival_count": self.seed_survival_count,
            "oldest_surviving_memory_cycle": self.oldest_surviving_memory_cycle,
            "mean_lifespan": self.mean_lifespan,
            "total_validated_citations": self.total_validated_citations,
            "self_reference_rate": self.self_reference_rate,
            "cumulative_model_calls": self.cumulative_model_calls,
            "cumulative_input_tokens": self.cumulative_input_tokens,
            "cumulative_output_tokens": self.cumulative_output_tokens,
        }


def secondary_metrics(
    state: MemoryState,
    *,
    budget_tokens: int,
    snapshots: Sequence[ArmCycleSnapshot],
    cumulative_calls: int = 0,
    cumulative_input_tokens: int = 0,
    cumulative_output_tokens: int = 0,
) -> SecondaryMetrics:
    """Compute every deterministic metric for one arm, without a single model call.

    Lifespan is measured only over memories that have actually retired. Averaging in
    the survivors would make an arm that forgot nothing look like an arm whose
    memories were short-lived.
    """
    active = state.active_memories
    retired = [m for m in state.memories if m.status in RETIRED_STATUSES]
    compressed = [m for m in retired if m.status is MemoryStatus.COMPRESSED]
    retirement_cycles = {
        memory.memory_id: _retirement_cycle(memory, snapshots) for memory in retired
    }
    lifespans = [
        cycle - memory.birth_cycle
        for memory in retired
        if (cycle := retirement_cycles[memory.memory_id]) is not None
    ]
    validated = sum(len(snapshot.validated_citations) for snapshot in snapshots)
    generated_cited = sum(
        1
        for snapshot in snapshots
        for citation in snapshot.validated_citations
        if _is_generated(state, citation.memory_id)
    )
    return SecondaryMetrics(
        active_tokens=state.active_tokens,
        free_tokens=max(0, budget_tokens - state.active_tokens),
        active_memory_count=len(active),
        retired_memory_count=len(retired),
        compressed_memory_count=len(compressed),
        seed_survival_count=sum(1 for m in active if m.memory_kind is MemoryKind.SEED),
        oldest_surviving_memory_cycle=min((m.birth_cycle for m in active), default=None),
        mean_lifespan=sum(lifespans) / len(lifespans) if lifespans else 0.0,
        total_validated_citations=validated,
        self_reference_rate=generated_cited / validated if validated else 0.0,
        cumulative_model_calls=cumulative_calls,
        cumulative_input_tokens=cumulative_input_tokens,
        cumulative_output_tokens=cumulative_output_tokens,
    )


def _is_generated(state: MemoryState, memory_id: str) -> bool:
    """Whether a cited memory was written by the arm rather than seeded into it."""
    memory = state.get(memory_id)
    return memory is not None and memory.memory_kind is not MemoryKind.SEED


def _retirement_cycle(memory: Memory, snapshots: Sequence[ArmCycleSnapshot]) -> int | None:
    """The cycle a memory left the active set, from the record that retired it."""
    for snapshot in snapshots:
        if any(record.memory_id == memory.memory_id for record in snapshot.retired_memories):
            return snapshot.cycle
    return None


def arm_of(snapshots: Sequence[ArmCycleSnapshot]) -> ArmId | None:
    """The arm a homogeneous run of snapshots belongs to, or None if mixed."""
    arms = {snapshot.arm_id for snapshot in snapshots}
    return arms.pop() if len(arms) == 1 else None
