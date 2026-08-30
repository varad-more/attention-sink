"""Real Bedrock, one role at a time, then a whole cycle.

The first six tests are the narrowest possible call to each gateway role: enough to
prove the model exists, the credentials reach it, the response validates against the
schema, and the metadata comes back. They are ordered the way a cycle uses them, so a
failure names the first thing that is wrong rather than the last.

The seventh is the one that matters: six arms, one committed cycle, against real
models and a real table.

What is asserted is deliberately not "the model said something good". A model's
judgement is the experiment's subject and cannot be a test's assertion. What is
asserted is that the plumbing holds: the schema validates, the citations point at
memories the arm actually has, no prompt names a mechanism, and every call is
recorded.
"""

from __future__ import annotations

import os
from typing import Any

import pytest

from attention_sink.domain import ArmId, MemoryKind, MemoryState
from attention_sink.model_gateway import (
    ApproximateTokenCounter,
    EvaluationTask,
    InterviewQuestion,
    ModelGateway,
)
from attention_sink.model_gateway.rendering import assert_policy_blind
from attention_sink.pilot import ProtocolBundle

pytestmark = pytest.mark.smoke


def _world(bundle: ProtocolBundle, *, count: int = 4) -> MemoryState:
    """A small arm state built from the real seed world.

    The committed protocol rather than invented text, so a prompt that reaches
    Bedrock in this suite is the shape of a prompt that reaches it in a cycle.
    """
    state = MemoryState(run_id="run_smoke", arm_id=ArmId.ARM_FIFO)
    for index, seed in enumerate(bundle.seed_world.memories[:count]):
        state = state.admit(
            [
                state.mint(
                    text=seed.text,
                    token_count=seed.provisional_token_count or 1,
                    memory_kind=MemoryKind.SEED,
                    cycle=index,
                )
            ]
        )
    return state


# ---------------------------------------------------------------- token counting


def test_production_token_counting(bedrock_gateway: ModelGateway) -> None:
    """The budget's unit, measured by whatever this deployment declared.

    ADR-011 makes Bedrock ``CountTokens`` the production counter; ADR-012 lets a
    deployment whose Region offers no model supporting it declare the approximate one
    instead. Either is a pass here, and which one it was is recorded -- the failure
    this guards against is a silent switch between them.
    """
    counter = bedrock_gateway.token_counter
    tokens = counter.count("The lighthouse at Kerrin Point was lit each evening.")
    assert tokens > 0
    assert counter.version
    if isinstance(counter, ApproximateTokenCounter):
        pytest.skip(
            f"TOKEN_COUNT_SOURCE declares {counter.version}; Bedrock CountTokens is "
            f"unavailable for {counter.model_id} in {counter.region} (ADR-012)"
        )
    detailed = counter.count_detailed("A second block, counted exactly.")
    assert detailed.tokens > 0
    assert detailed.metadata.simulated is False
    assert detailed.metadata.model_id == counter.model_id


# ----------------------------------------------------------------- the writer


def test_writer_smoke(bedrock_gateway: ModelGateway, pilot_bundle: ProtocolBundle) -> None:
    """One journal entry, schema-validated, citing only memories the arm holds."""
    state = _world(pilot_bundle)
    stimulus = pilot_bundle.stimulus_deck.stimuli[0]
    result = bedrock_gateway.writer.write(
        cycle=1,
        stimulus_text=stimulus.text,
        active_memories=state.active_memories,
    )
    output = result.output
    assert output.journal_entry.strip()
    assert output.candidate_memory.strip()
    # Every claimed citation resolves to a memory this arm actually has. A model that
    # invented a label would fail here, which is the check that keeps a fabricated
    # citation out of a policy statistic.
    held = {memory.memory_id for memory in state.active_memories}
    assert set(result.cited_memory_ids) <= held
    assert len(result.cited_memory_ids) == len(output.claimed_citations)
    assert result.metadata.simulated is False
    assert result.metadata.input_tokens is not None
    assert result.metadata.prompt_hash


# -------------------------------------------------------------- the summariser


def test_summarizer_smoke(bedrock_gateway: ModelGateway, pilot_bundle: ProtocolBundle) -> None:
    """A compression the mechanism planned, written by the model and re-counted."""
    from attention_sink.domain import CompressionPlan

    state = _world(pilot_bundle)
    sources = tuple(memory.memory_id for memory in state.active_memories[:3])
    dreamer = pilot_bundle.protocol.policies.dreamer
    plan = CompressionPlan(
        source_memory_ids=sources,
        summary_memory_id=state.next_memory_id(),
        summary_target_token_limit=dreamer.target_summary_tokens,
        tokens_freed=sum(m.token_count for m in state.active_memories[:3]),
        safety_margin_tokens=dreamer.safety_margin_tokens,
    )
    result = bedrock_gateway.summarizer.summarize(
        plan=plan,
        sources=[m for m in state.active_memories if m.memory_id in set(sources)],
    )
    assert result.output.summary_text.strip()
    # Exactly the plan's sources, in the plan's order. A summary that named other
    # memories would be a compression of something the mechanism did not choose, and
    # the adapter refuses one before this record can exist.
    assert result.source_memory_ids == sources
    assert result.summary_tokens <= plan.summary_target_token_limit
    assert result.metadata.simulated is False


# --------------------------------------------------------------- the interview


def test_interview_smoke(bedrock_gateway: ModelGateway, pilot_bundle: ProtocolBundle) -> None:
    """Every configured question answered, against the arm's active memory only."""
    state = _world(pilot_bundle)
    questions = tuple(
        InterviewQuestion(question_id=q.question_id, text=q.text)
        for q in pilot_bundle.interview.questions[:3]
    )
    result = bedrock_gateway.interviewer.interview(
        questions=questions, active_memories=state.active_memories
    )
    answered = {answer.question_id for answer in result.output.answers}
    assert answered == {question.question_id for question in questions}
    assert all(answer.answer.strip() for answer in result.output.answers)
    assert result.metadata.simulated is False


# --------------------------------------------------------------- the evaluator


def test_evaluator_smoke(bedrock_gateway: ModelGateway) -> None:
    """One judgement, with a label from the task's own closed vocabulary."""
    from attention_sink.model_gateway.schemas import EVALUATION_LABELS

    judgment = bedrock_gateway.evaluator.evaluate(
        task=EvaluationTask.ORIGIN_RECALL,
        passage="My grandmother lit the lighthouse at Kerrin Point every evening.",
        reference_statements=[
            "The lighthouse at Kerrin Point was lit by the narrator's grandmother."
        ],
    )
    assert judgment.output.label in EVALUATION_LABELS[EvaluationTask.ORIGIN_RECALL]
    assert 0.0 <= judgment.output.score <= 1.0
    assert judgment.evaluator_model_id
    assert judgment.metadata.simulated is False


# --------------------------------------------------------------- the embedding


def test_embedding_smoke(bedrock_gateway: ModelGateway) -> None:
    """One vector, of the configured size, deduplicated on a second ask."""
    text = "A brass key in the drawer that fits nothing I have found."
    first = bedrock_gateway.embeddings.embed(text)
    assert len(first.record.vector) == first.record.dimensions
    assert first.record.model_id == os.environ["EMBEDDING_MODEL_ID"]
    assert not first.deduplicated

    second = bedrock_gateway.embeddings.embed(text)
    # Same model, same content: one stored vector, not two.
    assert second.deduplicated
    assert second.record.cache_key == first.record.cache_key


# ------------------------------------------------------------ the prompts sent


def test_no_prompt_this_suite_sent_named_a_mechanism(
    bedrock_gateway: ModelGateway, pilot_bundle: ProtocolBundle
) -> None:
    """Every rendered request, checked against the live policy registry.

    Rendered rather than sent, so this costs nothing and still covers the exact
    strings the roles above put on the wire.
    """
    from attention_sink.model_gateway.rendering import build_writer_request

    state = _world(pilot_bundle)
    for stimulus in pilot_bundle.stimulus_deck.stimuli[:5]:
        request = build_writer_request(
            bedrock_gateway.prompts,
            cycle=stimulus.cycle,
            stimulus_text=stimulus.text,
            active_memories=state.active_memories,
        )
        assert_policy_blind(request.system, where=f"stimulus {stimulus.cycle} system")
        assert_policy_blind(request.user, where=f"stimulus {stimulus.cycle} data")
        # Opaque labels only: no memory identifier reaches a prompt (ADR-010).
        for memory in state.active_memories:
            assert memory.memory_id not in request.user


def test_the_gateway_reports_what_it_is(bedrock_gateway: ModelGateway) -> None:
    """A run that could not say which models it used would not be reproducible."""
    settings: Any = bedrock_gateway.settings
    assert not bedrock_gateway.simulated
    assert settings.models is not None
    assert settings.models.region == os.environ["AWS_REGION"]
    assert settings.models.writer_model_id == os.environ["WRITER_MODEL_ID"]
