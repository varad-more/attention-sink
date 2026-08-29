"""The deterministic stand-ins, and the one Bedrock class they stand in for."""

from __future__ import annotations

import pytest
from pydantic import BaseModel
from strands.agent.agent_result import AgentResult
from strands.models import BedrockModel
from strands.telemetry.metrics import EventLoopMetrics
from strands.types.event_loop import Usage
from strands.types.exceptions import StructuredOutputException

from attention_sink.model_gateway import (
    FIXTURE_MODEL_ID,
    SIMULATED_PREFIX,
    EvaluationOutput,
    EvaluationTask,
    FixtureEmbeddingProvider,
    FixtureInvoker,
    FixtureTokenCounter,
    FixtureUnavailableError,
    StrandsInvoker,
    ThoughtOutput,
)
from attention_sink.model_gateway.adapters import unpack_response

# ------------------------------------------------------------ the fixture seam


def test_a_schema_with_no_fixture_response_is_refused():
    class Unknown(BaseModel):
        value: int

    with pytest.raises(FixtureUnavailableError, match="Unknown"):
        FixtureInvoker().invoke(
            model_id="m",
            system="s",
            user="u",
            output_model=Unknown,
            temperature=0.5,
            top_p=0.9,
            max_tokens=64,
        )


def test_an_evaluation_request_that_names_no_task_is_refused():
    with pytest.raises(FixtureUnavailableError, match="names no task"):
        FixtureInvoker().invoke(
            model_id="m",
            system="s",
            user="no task line here",
            output_model=EvaluationOutput,
            temperature=0.5,
            top_p=0.9,
            max_tokens=64,
        )


def test_the_fixture_reports_plausible_token_counts():
    response = FixtureInvoker().invoke(
        model_id="m",
        system="instructions here",
        user="BOUNDARY abc\nMemories you hold:\n[m1] a lighthouse\nBOUNDARY abc",
        output_model=ThoughtOutput,
        temperature=0.5,
        top_p=0.9,
        max_tokens=64,
    )

    assert SIMULATED_PREFIX in response.output.journal_entry
    assert response.input_tokens is not None and response.input_tokens > 0
    assert response.output_tokens is not None and response.output_tokens > 0
    assert response.stop_reason == "end_turn"


# ------------------------------------------------------------- fixture counter


def test_the_fixture_counter_reports_the_heuristic_and_marks_itself():
    counter = FixtureTokenCounter()

    detailed = counter.count_detailed("four small words here")
    request = counter.count_request(system="two words", user="two more")

    assert counter.version == "heuristic-v1"
    assert counter.model_id == FIXTURE_MODEL_ID
    assert detailed.tokens == counter.count("four small words here")
    assert detailed.metadata.simulated is True
    assert detailed.metadata.input_tokens == detailed.tokens
    assert request.tokens == counter.count("two words\n\ntwo more")


# ---------------------------------------------------------- fixture embeddings


def test_fixture_embeddings_deduplicate_and_count_what_they_hold():
    provider = FixtureEmbeddingProvider(dimensions=256)

    provider.embed("one")
    provider.embed("one")
    provider.embed("two")

    assert provider.cached_count == 2


def test_fixture_embeddings_may_be_left_unnormalised():
    provider = FixtureEmbeddingProvider(dimensions=256, normalize=False)

    record = provider.embed("a memory").record

    assert record.normalized is False
    assert abs(sum(value * value for value in record.vector) - 1.0) > 1e-6


def test_fixture_embeddings_refuse_empty_text():
    with pytest.raises(ValueError, match="empty text"):
        FixtureEmbeddingProvider().embed(" \n ")


# --------------------------------------------------------- the Bedrock invoker


def agent_result(output: BaseModel | None) -> AgentResult:
    return AgentResult(
        stop_reason="end_turn",
        message={"role": "assistant", "content": [{"text": "x"}]},
        metrics=EventLoopMetrics(
            accumulated_usage=Usage(inputTokens=120, outputTokens=40, totalTokens=160)
        ),
        state=None,
        structured_output=output,
    )


def test_a_response_is_reduced_to_what_the_record_needs():
    thought = ThoughtOutput(journal_entry="an entry", candidate_memory="a memory")

    response = unpack_response(
        agent_result(thought), output_model=ThoughtOutput, model_id="writer-model"
    )

    assert response.output is thought
    assert response.stop_reason == "end_turn"
    assert response.input_tokens == 120
    assert response.output_tokens == 40


def test_a_result_carrying_no_structured_value_is_a_malformed_response():
    with pytest.raises(StructuredOutputException, match="ThoughtOutput"):
        unpack_response(agent_result(None), output_model=ThoughtOutput, model_id="writer-model")


def test_a_result_carrying_the_wrong_type_is_a_malformed_response():
    with pytest.raises(StructuredOutputException):
        unpack_response(
            agent_result(
                EvaluationOutput(task=EvaluationTask.ORIGIN_RECALL, label="absent", score=0.0)
            ),
            output_model=ThoughtOutput,
            model_id="writer-model",
        )


def test_the_agent_is_built_stateless_and_around_an_explicit_model():
    """The SDK resolves a Region-dependent default if a model is not named. ADR-006."""
    invoker = StrandsInvoker(region="eu-west-2", request_timeout_seconds=30)

    agent = invoker.build_agent(
        model_id="writer-model", system="instructions", temperature=0.3, top_p=0.8, max_tokens=512
    )

    model = agent.model
    assert isinstance(model, BedrockModel)
    config = model.config
    assert config["model_id"] == "writer-model"
    assert config["temperature"] == 0.3
    assert config["top_p"] == 0.8
    assert config["max_tokens"] == 512
    assert config["streaming"] is False
    assert agent.messages == []
    assert agent.system_prompt == "instructions"


def test_two_agents_from_one_invoker_share_no_conversation():
    invoker = StrandsInvoker(region="eu-west-2")

    first = invoker.build_agent(
        model_id="writer-model", system="s", temperature=0.3, top_p=0.8, max_tokens=64
    )
    first.messages.append({"role": "user", "content": [{"text": "remember this"}]})
    second = invoker.build_agent(
        model_id="writer-model", system="s", temperature=0.3, top_p=0.8, max_tokens=64
    )

    assert second.messages == []


def test_the_fixture_auditor_sustains_nothing_it_was_not_shown():
    """A claim naming a record outside the supplied set gets NONE, then is rejected."""
    from attention_sink.model_gateway import (
        ClaimedCitation,
        GatewaySettings,
        ModelInvocationError,
        build_gateway,
    )
    from tests.factories import world_state

    gateway = build_gateway(
        GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": "0"}), sleep=lambda _s: None
    )
    memories = world_state(count=1).active_memories

    with pytest.raises(ModelInvocationError):
        gateway.auditor.audit(
            journal_entry="the light was still burning",
            candidate_memory="the light was still burning",
            claims=[
                ClaimedCitation(
                    memory_ref="m9", supported_statement="nothing", journal_span="nothing"
                )
            ],
            active_memories=memories,
        )
