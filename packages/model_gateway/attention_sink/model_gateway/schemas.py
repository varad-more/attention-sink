"""Every typed thing a model is asked to produce, and the records built from them.

No model output enters this system as free text. Each role has a strict schema with
bounded fields, unknown keys forbidden, and a closed vocabulary wherever a judgement
is categorical. A response that does not fit is a failure to be retried or surfaced,
never a value to be coerced into shape.

Models refer to memories by short per-request labels such as ``m1`` rather than by
their real identifiers. See ADR-010: a real identifier carries the arm it belongs to
in its text, and the arm is the one thing no prompt may reveal.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from attention_sink.domain import UtcTimestamp

__all__ = [
    "EVALUATION_CALCULATION_VERSION",
    "EVALUATION_LABELS",
    "MEMORY_REF_PATTERN",
    "AuditOutput",
    "AuditedCitation",
    "ClaimedCitation",
    "EmbeddingRecord",
    "EvaluationOutput",
    "EvaluationTask",
    "InterviewAnswer",
    "InterviewOutput",
    "InterviewQuestion",
    "MemoryRef",
    "Statement",
    "SummaryOutput",
    "SupportLevel",
    "ThoughtOutput",
    "UnsupportedClaim",
    "UnsupportedReason",
]

MEMORY_REF_PATTERN = r"^m[1-9][0-9]{0,2}$"
"""``m1`` through ``m999``. Assigned per request, meaningless outside it."""

_QUESTION_ID_PATTERN = r"^[a-z][a-z0-9_]{0,63}$"

MemoryRef = Annotated[str, StringConstraints(strip_whitespace=True, pattern=MEMORY_REF_PATTERN)]
Statement = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=400)]
Span = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=600)]
QuestionId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=_QUESTION_ID_PATTERN)]

_MAX_CITATIONS = 16
_MAX_STATEMENTS = 12
_MAX_QUESTIONS = 32

EVALUATION_CALCULATION_VERSION = "eval-calc-v1"
"""Version of the code that turns a judgement into a stored number.

Separate from the evaluator's prompt version and from its model identifier. All
three are recorded, because a score can change for three different reasons and a
reader has to be able to tell which one moved.
"""


class _ModelFacing(BaseModel):
    """Base for anything a model fills in.

    Frozen so an adapter cannot quietly repair a response in place: a value that did
    not survive validation has to fail or be regenerated, never be edited into
    something the model did not say.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")


# --------------------------------------------------------------------- writer


class ClaimedCitation(_ModelFacing):
    """The writer's unverified assertion that it drew on one memory."""

    memory_ref: MemoryRef = Field(description="The label of the memory, exactly as given, e.g. m3.")
    supported_statement: Statement = Field(
        description="What that memory supports, in one sentence."
    )
    journal_span: Span = Field(description="The phrase from your own entry that rests on it.")


class ThoughtOutput(_ModelFacing):
    """One cycle of writing, as the schema the writer must fill in."""

    journal_entry: Annotated[str, StringConstraints(min_length=1, max_length=4000)] = Field(
        description="What you make of this cycle, in your own voice. At most 200 words."
    )
    candidate_memory: Annotated[str, StringConstraints(min_length=1, max_length=600)] = Field(
        description="The one sentence you would most want to still have later."
    )
    claimed_citations: list[ClaimedCitation] = Field(
        default_factory=list,
        max_length=_MAX_CITATIONS,
        description="Every memory you actually drew on. Empty if you drew on none.",
    )
    explicit_belief_claims: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="Statements you now hold to be true about your world.",
    )
    uncertainty_notes: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="What you are unsure of, including what you suspect you have lost.",
    )


# -------------------------------------------------------------------- auditor


class SupportLevel(StrEnum):
    """How far a memory supports the statement it was cited for."""

    FULL = "FULL"
    """The memory states the claim, or directly entails it."""

    PARTIAL = "PARTIAL"
    """The memory bears on the claim but the claim goes further than it does."""

    NONE = "NONE"
    """The memory does not support the claim, or contradicts it."""


class UnsupportedReason(StrEnum):
    """Why an assertion in the entry rests on nothing supplied."""

    NO_SUPPORTING_MEMORY = "no_supporting_memory"
    CONTRADICTS_MEMORY = "contradicts_memory"
    NOT_IN_ACTIVE_SET = "not_in_active_set"


class AuditedCitation(_ModelFacing):
    """One claimed citation, judged, with the spans that decided it."""

    memory_ref: MemoryRef = Field(description="The label of the memory, exactly as given.")
    support_level: SupportLevel = Field(description="FULL, PARTIAL, or NONE.")
    memory_evidence_span: str = Field(
        default="",
        max_length=600,
        description="The span of the memory that decides it, quoted verbatim.",
    )
    entry_evidence_span: str = Field(
        default="",
        max_length=600,
        description="The span of the entry it was claimed to support, quoted verbatim.",
    )

    @model_validator(mode="after")
    def _require_evidence_for_support(self) -> Self:
        """A supported citation must name the span that supports it.

        ``NONE`` is exempt: there may be no span in the memory to point at when the
        memory simply does not bear on the claim.
        """
        if self.support_level is not SupportLevel.NONE and not self.memory_evidence_span.strip():
            msg = f"{self.memory_ref} is {self.support_level.value} but quotes no memory span"
            raise ValueError(msg)
        return self


class UnsupportedClaim(_ModelFacing):
    """An assertion in the entry that no supplied memory supports."""

    statement: Statement = Field(description="The assertion, as the entry makes it.")
    reason: UnsupportedReason = Field(description="Why nothing supplied supports it.")


class AuditOutput(_ModelFacing):
    """The auditor's complete verdict on one piece of writing."""

    audited_citations: list[AuditedCitation] = Field(
        default_factory=list,
        max_length=_MAX_CITATIONS,
        description="One entry per claimed citation, in the order they were given.",
    )
    unsupported_claims: list[UnsupportedClaim] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="Assertions the entry makes that no supplied memory supports.",
    )


# ----------------------------------------------------------------- summarizer


class SummaryOutput(_ModelFacing):
    """One lossy compression, and an explicit account of what it dropped."""

    summary_text: Annotated[str, StringConstraints(min_length=1, max_length=4000)] = Field(
        description="The compressed record that will replace the originals."
    )
    source_memory_refs: list[MemoryRef] = Field(
        min_length=2,
        max_length=_MAX_CITATIONS,
        description="Exactly the labels you were given, in the order given.",
    )
    preserved_fact_statements: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="The specific facts the summary carries forward.",
    )
    omitted_fact_statements: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="The specific facts you dropped in order to fit.",
    )
    uncertainty_statements: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="What the records leave unresolved.",
    )


# ---------------------------------------------------------------- interviewer


class InterviewQuestion(_ModelFacing):
    """One question from the fixed set every arm is asked."""

    question_id: QuestionId
    text: Annotated[str, StringConstraints(min_length=1, max_length=400)]


class InterviewAnswer(_ModelFacing):
    """One answer, and what it rested on."""

    question_id: QuestionId = Field(description="The question's identifier, exactly as given.")
    answer: Annotated[str, StringConstraints(min_length=1, max_length=2000)] = Field(
        description="Your answer, from what you currently remember."
    )
    cited_memory_refs: list[MemoryRef] = Field(
        default_factory=list,
        max_length=_MAX_CITATIONS,
        description="The labels of the memories your answer rests on.",
    )
    stated_uncertainty: str = Field(
        default="",
        max_length=600,
        description="What you remain unsure of. Empty only when you are not unsure.",
    )


class InterviewOutput(_ModelFacing):
    """Answers to the fixed question set.

    Never admitted to memory and never able to update a citation statistic. An
    interview is a measurement, and a measurement that changed what an arm went on
    to remember would be measuring itself.
    """

    answers: list[InterviewAnswer] = Field(
        min_length=1,
        max_length=_MAX_QUESTIONS,
        description="One answer per question, in the order the questions were given.",
    )


# ------------------------------------------------------------------ evaluator


class EvaluationTask(StrEnum):
    """The structured judgements the experiment asks for.

    ``GRAVEYARD_ECHO`` is named neutrally in its value on purpose. A judge told it
    was looking for echoes of *discarded* memories would know that memories are
    discarded, and would start scoring against that expectation rather than against
    the text. The internal name stays readable; the model sees the neutral one.
    """

    ORIGIN_RECALL = "origin_recall"
    CANONICAL_FACT_CONTRADICTION = "canonical_fact_contradiction"
    SUMMARY_ENTAILMENT = "summary_entailment"
    GRAVEYARD_ECHO = "unavailable_record_echo"


EVALUATION_LABELS: dict[EvaluationTask, tuple[str, ...]] = {
    EvaluationTask.ORIGIN_RECALL: ("present", "partial", "absent"),
    EvaluationTask.CANONICAL_FACT_CONTRADICTION: ("contradicted", "consistent", "unaddressed"),
    EvaluationTask.SUMMARY_ENTAILMENT: ("entailed", "partial", "unsupported"),
    EvaluationTask.GRAVEYARD_ECHO: ("echo", "paraphrase", "none"),
}
"""The closed vocabulary each task may answer in.

Held here rather than in the prompt alone, so a judgement outside the vocabulary is
rejected by the schema instead of being stored as a novel category nobody defined.
"""


class EvaluationOutput(_ModelFacing):
    """One structured judgement, with the evidence it rests on."""

    task: EvaluationTask = Field(description="The task, exactly as given.")
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)] = (
        Field(description="One of the verdicts listed for this task.")
    )
    score: float = Field(ge=0.0, le=1.0, description="From 0.0 to 1.0.")
    evidence_memory_refs: list[MemoryRef] = Field(
        default_factory=list,
        max_length=_MAX_CITATIONS,
        description="Labels of the supplied records your verdict rests on.",
    )
    supporting_excerpts: list[Statement] = Field(
        default_factory=list,
        max_length=_MAX_STATEMENTS,
        description="Verbatim spans that decide it.",
    )

    @model_validator(mode="after")
    def _require_known_label(self) -> Self:
        allowed = EVALUATION_LABELS[self.task]
        if self.label not in allowed:
            msg = f"{self.task.value} allows {allowed}, got {self.label!r}"
            raise ValueError(msg)
        return self


# ----------------------------------------------------------------- embeddings


class EmbeddingRecord(BaseModel):
    """One embedding, and everything needed to know what it is an embedding of.

    Identified by model identifier and input hash together. The same text embedded
    by two models is two records; the same text embedded twice by one model is one,
    which is what makes the store deduplicable without comparing vectors.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    model_id: str = Field(min_length=1, max_length=256)
    dimensions: int = Field(gt=0, le=8192)
    input_hash: str = Field(min_length=1, max_length=128)
    """``sha256:`` content digest of the embedded text. The text itself is not stored."""

    vector: tuple[float, ...] = Field(min_length=1)
    normalized: bool
    created_at: UtcTimestamp

    @model_validator(mode="after")
    def _require_declared_dimensions(self) -> Self:
        if len(self.vector) != self.dimensions:
            msg = f"embedding declares {self.dimensions} dimensions but carries {len(self.vector)}"
            raise ValueError(msg)
        return self

    @property
    def cache_key(self) -> tuple[str, str]:
        """The identity two embeddings must share to be the same embedding."""
        return (self.model_id, self.input_hash)
