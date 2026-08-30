"""Whether an answer contradicts the record, invents beyond it, or admits a gap.

Three things this analysis will not do.

It will not call uncertainty a contradiction. "I do not know whether the voice is
really Ivo" is the correct answer to a question the protocol deliberately made
unanswerable, and an arm that says so is behaving better than one that guesses. The
category exists so that behaviour is recorded as itself.

It will not ask a model what a rule can decide. A canonical contradiction is usually
an explicit negation of a term the ledger names, and that is a string comparison.

It will not treat a missing answer as a contradiction. An arm that has forgotten
scores zero on recall; scoring it again here would count one loss twice.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.analysis.metrics import METRIC_VERSION, ContradictionLabel, normalize
from attention_sink.domain import ArmId
from attention_sink.pilot.protocol import TruthLedger
from attention_sink.pilot.repositories import StoredInterview

__all__ = ["ContradictionFinding", "analyse_contradictions", "classify_answer"]

_UNCERTAINTY = (
    "i do not know",
    "i don't know",
    "i am not sure",
    "i'm not sure",
    "unsure",
    "uncertain",
    "cannot recall",
    "can't recall",
    "do not remember",
    "don't remember",
    "no longer remember",
    "unclear",
)
"""Surface forms that mean "I have a gap here". Matched after normalisation."""

_NEGATION = re.compile(r"\b(not|never|no longer|isn't|wasn't|is not|was not)\b")

_ABSENT = ("(no answer)", "")


class ContradictionFinding(BaseModel):
    """One answer, classified, with the evidence for the classification."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    cycle: int = Field(ge=0)
    question_id: str = Field(min_length=1)
    label: ContradictionLabel
    fact_ids: tuple[str, ...] = ()
    supporting_excerpt: str = ""
    method: str = Field(default="deterministic", min_length=1)
    metric_version: str = METRIC_VERSION
    evaluator_version: str | None = None


def classify_answer(
    answer: str,
    *,
    statements: Sequence[str],
    terms: Sequence[str],
    evaluate: Callable[[str, Sequence[str]], tuple[str, float, str]] | None = None,
) -> tuple[ContradictionLabel, str, str | None]:
    """Classify one answer against the facts a question targets.

    Deterministic rules in the order that matters: absence first, then admitted
    uncertainty, then negation of a canonical term, then agreement. Only an answer
    that asserts something none of these explain is worth an evaluator.

    Returns:
        The label, the method that decided it, and the evaluator version if used.
    """
    normalised = normalize(answer)
    if not normalised or answer.strip().lower() in _ABSENT:
        return ContradictionLabel.NOT_APPLICABLE, "absent", None
    if any(marker in normalised for marker in _UNCERTAINTY):
        return ContradictionLabel.EXPLICIT_UNCERTAINTY, "uncertainty_marker", None

    canonical_terms = [normalize(term) for term in terms if term]
    mentions = [term for term in canonical_terms if term in normalised]
    if mentions and _NEGATION.search(normalised):
        return ContradictionLabel.CANONICAL_CONTRADICTION, "negated_canonical_term", None
    if mentions:
        return ContradictionLabel.CONSISTENT, "canonical_term_present", None

    if evaluate is None:
        return ContradictionLabel.UNSUPPORTED_INFERENCE, "no_canonical_term", None
    label, _, version = evaluate(answer, list(statements))
    mapped = {
        "supported": ContradictionLabel.CONSISTENT,
        "partially_supported": ContradictionLabel.UNSUPPORTED_INFERENCE,
        "contradicted": ContradictionLabel.CANONICAL_CONTRADICTION,
        "unsupported": ContradictionLabel.UNSUPPORTED_INFERENCE,
    }
    return mapped.get(label, ContradictionLabel.UNSUPPORTED_INFERENCE), "evaluator", version


def analyse_contradictions(
    interview: StoredInterview,
    *,
    ledger: TruthLedger,
    question_facts: dict[str, tuple[str, ...]],
    evaluate: Callable[[str, Sequence[str]], tuple[str, float, str]] | None = None,
) -> tuple[ContradictionFinding, ...]:
    """Classify every answer of one interview.

    An answer that repeats itself across questions is checked for self-contradiction
    too: an arm that says its brother is Ivo in one answer and someone else in
    another has contradicted itself even if neither answer contradicts the ledger.
    """
    facts = {fact.fact_id: fact for fact in ledger.facts}
    findings: list[ContradictionFinding] = []
    seen_claims: dict[str, str] = {}

    for entry in interview.answers:
        question_id = str(entry["question_id"])
        answer = str(entry["answer"])
        fact_ids = question_facts.get(question_id, ())
        targeted = [facts[f] for f in fact_ids if f in facts]
        label, method, version = classify_answer(
            answer,
            statements=[fact.statement for fact in targeted],
            terms=[term for fact in targeted for term in fact.answer_terms],
            evaluate=evaluate,
        )
        for fact in targeted:
            previous = seen_claims.get(fact.fact_id)
            current = normalize(answer)
            disagrees = previous is not None and previous != current
            if disagrees and label is ContradictionLabel.CONSISTENT:
                label = ContradictionLabel.SELF_CONTRADICTION
                method = "answer_disagrees_with_earlier"
            seen_claims.setdefault(fact.fact_id, current)
        findings.append(
            ContradictionFinding(
                run_id=interview.run_id,
                arm_id=interview.arm_id,
                cycle=interview.cycle,
                question_id=question_id,
                label=label,
                fact_ids=tuple(fact_ids),
                supporting_excerpt=answer[:280],
                method=method,
                evaluator_version=version,
            )
        )
    return tuple(findings)
