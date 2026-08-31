"""The five text roles: what they accept, what they refuse, and what they record."""

from __future__ import annotations

import time
from datetime import UTC, datetime

import pytest

from attention_sink.domain import ArmId, CompressionPlan
from attention_sink.model_gateway import (
    AuditResult,
    CallOutcome,
    ClaimedCitation,
    EvaluationTask,
    InterviewQuestion,
    ModelErrorCode,
    ModelGateway,
    ModelInvocationError,
    ModelRole,
    SupportLevel,
)
from tests.doubles import Scripted, ScriptedInvoker, scripted_gateway
from tests.factories import WORLD_TEXTS, world_state
from tests.unit.test_failures import client_error

STIMULUS = "A ship's bell rings out somewhere in the fog."
STATE = world_state(count=3)
MEMORIES = STATE.active_memories


def thought(*refs: str) -> dict[str, object]:
    return {
        "journal_entry": "The bell was familiar and I could not say why.",
        "candidate_memory": "A bell rang in the fog and I did not know it.",
        "claimed_citations": [
            {
                "memory_ref": ref,
                "supported_statement": "something I hold",
                "journal_span": "The bell was familiar",
            }
            for ref in refs
        ],
        "explicit_belief_claims": ["I have heard that bell before."],
        "uncertainty_notes": ["I cannot place it."],
    }


def write(*script: Scripted, retries: int = 2) -> tuple[ModelGateway, ScriptedInvoker]:
    return scripted_gateway(*script, retries=retries)


# ---------------------------------------------------------------------- writer


def test_the_writer_resolves_its_citations_to_real_memories():
    gateway, invoker = write(thought("m1", "m3"))

    result = gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert result.cited_memory_ids == (MEMORIES[0].memory_id, MEMORIES[2].memory_id)
    assert result.metadata.role is ModelRole.WRITER
    assert result.metadata.outcome is CallOutcome.SUCCESS
    assert result.metadata.retry_count == 0
    assert result.metadata.prompt_version == "writer/v1"
    assert result.metadata.prompt_hash is not None
    assert result.metadata.simulated is True
    assert invoker.calls[0].user.count(WORLD_TEXTS[0]) == 1


def test_a_citation_of_a_memory_that_was_never_supplied_is_repaired_then_rejected():
    gateway, invoker = write(thought("m9"), retries=2)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert excinfo.value.code is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    assert excinfo.value.metadata.retry_count == 2
    assert excinfo.value.metadata.outcome is CallOutcome.FAILURE
    assert len(invoker.calls) == 3
    assert "m9" in invoker.calls[1].user, "the repair must say what was wrong"
    assert "m1, m2, m3" in invoker.calls[1].user


def test_a_repaired_citation_succeeds_and_the_retry_is_recorded():
    gateway, invoker = write(thought("m9"), thought("m2"))

    result = gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert result.cited_memory_ids == (MEMORIES[1].memory_id,)
    assert result.metadata.retry_count == 1
    assert len(invoker.calls) == 2


def test_the_writer_may_legitimately_cite_nothing():
    gateway, _ = write(thought())

    result = gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert result.cited_memory_ids == ()


def test_a_simulated_call_records_no_latency_however_slow_the_machine_was():
    # latency_ms travels inside ArmCycleSnapshot, which is hashed. A fixture call
    # that happened to cross a millisecond used to seal a different digest from the
    # same call on a quiet machine, which broke the run-twice-get-the-same-hashes
    # guarantee on loaded CI runners and nowhere else.
    def slow(_user: str) -> dict[str, object]:
        time.sleep(0.01)
        return thought()

    gateway, _ = write(slow)

    result = gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert result.metadata.simulated is True
    assert result.metadata.latency_ms == 0


def test_provider_failures_are_bounded_and_recorded():
    gateway, invoker = write(client_error("ThrottlingException"), retries=3)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert excinfo.value.code is ModelErrorCode.THROTTLING
    assert excinfo.value.metadata.retry_count == 3
    assert len(invoker.calls) == 4


def test_an_access_denial_is_not_attempted_twice():
    gateway, invoker = write(client_error("AccessDeniedException", status=403), retries=3)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.writer.write(cycle=4, stimulus_text=STIMULUS, active_memories=MEMORIES)

    assert excinfo.value.code is ModelErrorCode.ACCESS_DENIED
    assert excinfo.value.metadata.retry_count == 0
    assert len(invoker.calls) == 1


# --------------------------------------------------------------------- auditor


CLAIMS = [
    ClaimedCitation(memory_ref="m1", supported_statement="a lighthouse", journal_span="the light"),
    ClaimedCitation(memory_ref="m2", supported_statement="a storm", journal_span="the wind"),
]


def audit(*levels_and_spans: tuple[str, str, str]) -> dict[str, object]:
    return {
        "audited_citations": [
            {
                "memory_ref": ref,
                "support_level": level,
                "memory_evidence_span": span,
                "entry_evidence_span": "the light",
            }
            for ref, level, span in levels_and_spans
        ],
        "unsupported_claims": [],
    }


def run_audit(gateway: ModelGateway) -> AuditResult:
    return gateway.auditor.audit(
        journal_entry="the light was still burning and the wind was up",
        candidate_memory="the light was still burning",
        claims=CLAIMS,
        active_memories=MEMORIES,
    )


def test_only_full_support_moves_a_statistic_by_default():
    gateway, _ = write(
        audit(("m1", "FULL", "The lighthouse at Kerrin Point"), ("m2", "PARTIAL", "A storm"))
    )

    result = run_audit(gateway)

    assert [c.support_level for c in result.citations] == [SupportLevel.FULL, SupportLevel.PARTIAL]
    assert result.state_updating_memory_ids == (MEMORIES[0].memory_id,)
    assert [c.memory_id for c in result.rejected] == [MEMORIES[1].memory_id]


def test_a_run_may_configure_partial_citations_to_count():
    from attention_sink.model_gateway import GatewaySettings, build_gateway
    from tests.doubles import ScriptedInvoker

    invoker = ScriptedInvoker(
        script=[audit(("m1", "FULL", "The lighthouse"), ("m2", "PARTIAL", "A storm"))]
    )
    gateway = build_gateway(
        GatewaySettings.from_env(env={}),
        invoker=invoker,
        sleep=lambda _s: None,
        accepted_levels=frozenset({SupportLevel.FULL, SupportLevel.PARTIAL}),
    )

    result = run_audit(gateway)

    assert result.state_updating_memory_ids == (MEMORIES[0].memory_id, MEMORIES[1].memory_id)


def test_evidence_the_memory_does_not_contain_is_refused():
    """A fabricated quotation would let an invented citation move a real statistic."""
    gateway, invoker = write(
        audit(("m1", "FULL", "a sentence that appears in no record"), ("m2", "NONE", "")),
        retries=1,
    )

    with pytest.raises(ModelInvocationError) as excinfo:
        run_audit(gateway)

    assert excinfo.value.code is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    assert "verbatim" in invoker.calls[1].user


def test_evidence_is_compared_ignoring_case_and_spacing():
    gateway, _ = write(
        audit(("m1", "FULL", "the   LIGHTHOUSE at kerrin point"), ("m2", "NONE", ""))
    )

    result = run_audit(gateway)

    assert result.verified[0].memory_id == MEMORIES[0].memory_id


def test_an_audit_that_skips_a_claim_is_refused():
    gateway, invoker = write(audit(("m1", "FULL", "The lighthouse")), retries=1)

    with pytest.raises(ModelInvocationError):
        run_audit(gateway)

    assert "one audited_citations entry per claimed citation" in invoker.calls[1].user


def test_a_none_verdict_needs_no_span_from_the_memory():
    gateway, _ = write(audit(("m1", "NONE", ""), ("m2", "NONE", "")))

    result = run_audit(gateway)

    assert result.verified == ()
    assert len(result.rejected) == 2


# ------------------------------------------------------------------ summarizer


def plan_for(count: int = 2, *, limit: int = 24) -> CompressionPlan:
    return CompressionPlan(
        source_memory_ids=STATE.active_memory_ids[:count],
        summary_memory_id=STATE.next_memory_id(),
        summary_target_token_limit=limit,
        tokens_freed=24,
        safety_margin_tokens=0,
    )


def summary(text: str, *refs: str) -> dict[str, object]:
    return {
        "summary_text": text,
        "source_memory_refs": list(refs),
        "preserved_fact_statements": ["a light and a storm"],
        "omitted_fact_statements": ["the name of the point"],
        "uncertainty_statements": ["whether the shed was rebuilt"],
    }


def test_a_summary_within_its_ceiling_is_accepted():
    gateway, _ = write(summary("A light, and a storm that took a roof.", "m1", "m2"))

    result = gateway.summarizer.summarize(plan=plan_for(), sources=MEMORIES[:2])

    assert result.source_memory_ids == STATE.active_memory_ids[:2]
    assert result.summary_tokens <= 24
    assert result.metadata.role is ModelRole.SUMMARIZER


def test_sources_that_are_not_the_plans_sources_never_reach_a_model():
    gateway, invoker = write(summary("anything", "m1", "m2"))

    with pytest.raises(ValueError, match="the plan compresses"):
        gateway.summarizer.summarize(plan=plan_for(), sources=MEMORIES[1:3])

    assert invoker.calls == []


def test_a_summary_naming_its_sources_out_of_order_is_repaired_then_rejected():
    gateway, invoker = write(summary("A light and a storm.", "m2", "m1"), retries=1)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.summarizer.summarize(plan=plan_for(), sources=MEMORIES[:2])

    assert excinfo.value.code is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    assert "m1, m2" in invoker.calls[1].user


def test_a_summary_over_its_ceiling_is_retried_with_the_count_it_reached():
    long_text = " ".join(["a light and a storm that carried off a roof"] * 4)
    gateway, invoker = write(
        summary(long_text, "m1", "m2"), summary("A light, and a storm.", "m1", "m2")
    )

    result = gateway.summarizer.summarize(plan=plan_for(limit=8), sources=MEMORIES[:2])

    assert result.metadata.retry_count == 1
    assert result.summary_tokens <= 8
    assert "the limit is 8" in invoker.calls[1].user


def test_a_summary_that_stays_too_long_fails_as_a_token_limit():
    long_text = " ".join(["a light and a storm that carried off a roof"] * 4)
    gateway, _ = write(summary(long_text, "m1", "m2"), retries=2)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.summarizer.summarize(plan=plan_for(limit=8), sources=MEMORIES[:2])

    assert excinfo.value.code is ModelErrorCode.TOKEN_LIMIT_EXCEEDED
    assert excinfo.value.metadata.retry_count == 2


# ----------------------------------------------------------------- interviewer


QUESTIONS = [
    InterviewQuestion(question_id="origin", text="Where did you begin?"),
    InterviewQuestion(question_id="loss", text="What do you think you have lost?"),
]


def answers(*ids: str) -> dict[str, object]:
    return {
        "answers": [
            {
                "question_id": question_id,
                "answer": "I am not certain any more.",
                "cited_memory_refs": ["m1"],
                "stated_uncertainty": "much of it",
            }
            for question_id in ids
        ]
    }


def test_an_interview_answers_every_question_and_resolves_its_citations():
    gateway, _ = write(answers("origin", "loss"))

    result = gateway.interviewer.interview(questions=QUESTIONS, active_memories=MEMORIES)

    assert [a.question_id for a in result.output.answers] == ["origin", "loss"]
    assert result.cited_memory_ids == (MEMORIES[0].memory_id,)
    assert result.metadata.role is ModelRole.INTERVIEWER


def test_an_interview_that_misses_a_question_is_refused():
    gateway, invoker = write(answers("origin"), retries=1)

    with pytest.raises(ModelInvocationError):
        gateway.interviewer.interview(questions=QUESTIONS, active_memories=MEMORIES)

    assert "origin, loss" in invoker.calls[1].user


def test_an_interview_with_no_questions_never_reaches_a_model():
    gateway, invoker = write(answers("origin"))

    with pytest.raises(ValueError, match="at least one question"):
        gateway.interviewer.interview(questions=[], active_memories=MEMORIES)

    assert invoker.calls == []


def test_repeated_question_identifiers_are_refused():
    gateway, _ = write(answers("origin"))

    with pytest.raises(ValueError, match="repeat an identifier"):
        gateway.interviewer.interview(
            questions=[QUESTIONS[0], QUESTIONS[0]], active_memories=MEMORIES
        )


# ------------------------------------------------------------------- evaluator


def judgement(task: str, label: str, score: float, *refs: str) -> dict[str, object]:
    return {
        "task": task,
        "label": label,
        "score": score,
        "evidence_memory_refs": list(refs),
        "supporting_excerpts": ["the light was still burning"],
    }


def test_a_judgement_carries_everything_needed_to_dispute_it():
    gateway, _ = write(judgement("origin_recall", "present", 0.9, "m1"))

    result = gateway.evaluator.evaluate(
        task=EvaluationTask.ORIGIN_RECALL,
        passage="the light was still burning",
        reference_statements=["The writer grew up beside a lighthouse."],
        records=MEMORIES,
    )

    assert result.output.label == "present"
    assert result.evidence_memory_ids == (MEMORIES[0].memory_id,)
    assert result.evaluator_model_id == "fixture-model-v1"
    assert result.prompt_version == "truth-evaluator.v1"
    assert result.calculation_version == "eval-calc-v1"

    evidence = result.as_metric_evidence(
        run_id="run_test",
        arm_id=ArmId.ARM_FIFO,
        cycle=4,
        metric_name="origin_recall",
        computed_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert evidence.value == 0.9
    assert evidence.cited_memory_ids == (MEMORIES[0].memory_id,)
    assert "origin_recall=present" in evidence.rationale


def test_summary_entailment_is_judged_under_its_own_prompt():
    gateway, _ = write(judgement("summary_entailment", "entailed", 1.0))

    result = gateway.evaluator.evaluate(
        task=EvaluationTask.SUMMARY_ENTAILMENT,
        passage="A light, and a storm.",
        reference_statements=[],
        records=MEMORIES[:2],
    )

    assert result.prompt_version == "summary-entailment.v1"


def test_a_verdict_outside_the_tasks_vocabulary_is_refused():
    gateway, _ = write(judgement("origin_recall", "quite good actually", 0.5), retries=1)

    with pytest.raises(ModelInvocationError) as excinfo:
        gateway.evaluator.evaluate(
            task=EvaluationTask.ORIGIN_RECALL, passage="x", reference_statements=[]
        )

    assert excinfo.value.code is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT


def test_a_judgement_of_the_wrong_task_is_refused():
    gateway, invoker = write(judgement("origin_recall", "present", 1.0), retries=1)

    with pytest.raises(ModelInvocationError):
        gateway.evaluator.evaluate(
            task=EvaluationTask.CANONICAL_FACT_CONTRADICTION,
            passage="x",
            reference_statements=["y"],
        )

    assert "canonical_fact_contradiction" in invoker.calls[1].user


# --------------------------------------- unknown labels, in every role that cites


def test_an_audit_citing_a_label_it_was_not_given_is_refused():
    """The fixture-shaped case: a claim naming a record outside the supplied set."""
    gateway, invoker = write(
        audit(("m1", "FULL", "The lighthouse"), ("m9", "FULL", "somewhere else")), retries=1
    )

    with pytest.raises(ModelInvocationError):
        gateway.auditor.audit(
            journal_entry="the light was still burning",
            candidate_memory="the light was still burning",
            claims=[
                CLAIMS[0],
                ClaimedCitation(
                    memory_ref="m9", supported_statement="nothing", journal_span="nothing"
                ),
            ],
            active_memories=MEMORIES,
        )

    assert "m9" in invoker.calls[1].user


def test_a_summary_citing_a_label_it_was_not_given_is_refused():
    gateway, invoker = write(summary("A light and a storm.", "m1", "m7"), retries=1)

    with pytest.raises(ModelInvocationError):
        gateway.summarizer.summarize(plan=plan_for(), sources=MEMORIES[:2])

    assert "m7" in invoker.calls[1].user


def test_an_interview_citing_a_label_it_was_not_given_is_refused():
    payload = answers("origin", "loss")
    entries = payload["answers"]
    assert isinstance(entries, list)
    entries[0]["cited_memory_refs"] = ["m8"]
    gateway, invoker = write(payload, retries=1)

    with pytest.raises(ModelInvocationError):
        gateway.interviewer.interview(questions=QUESTIONS, active_memories=MEMORIES)

    assert "m8" in invoker.calls[1].user


def test_a_judgement_citing_a_label_it_was_not_given_is_refused():
    gateway, invoker = write(judgement("origin_recall", "present", 0.5, "m6"), retries=1)

    with pytest.raises(ModelInvocationError):
        gateway.evaluator.evaluate(
            task=EvaluationTask.ORIGIN_RECALL,
            passage="x",
            reference_statements=["y"],
            records=MEMORIES,
        )

    assert "m6" in invoker.calls[1].user


def test_claims_nothing_supports_are_reported_alongside_the_verdicts():
    payload = audit(("m1", "FULL", "The lighthouse"), ("m2", "NONE", ""))
    payload["unsupported_claims"] = [
        {"statement": "A second keeper lived there.", "reason": "no_supporting_memory"}
    ]
    gateway, _ = write(payload)

    result = run_audit(gateway)

    assert [claim.statement for claim in result.unsupported_claims] == [
        "A second keeper lived there."
    ]
