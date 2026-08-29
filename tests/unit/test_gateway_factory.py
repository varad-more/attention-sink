"""What a process talks to is decided once, from validated configuration."""

from __future__ import annotations

import pytest

from attention_sink.model_gateway import (
    BEDROCK_COUNTER_VERSION,
    FIXTURE_MODEL_ID,
    BedrockEmbeddingProvider,
    BedrockTokenCounter,
    CitationAuditor,
    ClaimEvaluator,
    ConfigurationError,
    EmbeddingProvider,
    FixtureEmbeddingProvider,
    FixtureTokenCounter,
    GatewaySettings,
    Interviewer,
    MemorySummarizer,
    RuntimeMode,
    RuntimeSettings,
    StrandsInvoker,
    StructuredCaller,
    StructuredThoughtWriter,
    ThoughtWriter,
    build_gateway,
)
from tests.doubles import FakeRuntime
from tests.unit.test_gateway_settings import BEDROCK_ENV


def caller_of(role: object) -> StructuredCaller:
    """The caller behind one role adapter.

    The gateway exposes roles as protocols, which is what the rest of the system
    should see. A wiring test has to look behind that, and does so by narrowing to
    the concrete adapter rather than by widening the protocol.
    """
    assert hasattr(role, "caller")
    caller = role.caller
    assert isinstance(caller, StructuredCaller)
    return caller


def test_fixture_mode_assembles_without_any_aws_configuration():
    gateway = build_gateway(GatewaySettings.from_env(env={}))

    assert gateway.simulated is True
    assert isinstance(gateway.writer, ThoughtWriter)
    assert isinstance(gateway.auditor, CitationAuditor)
    assert isinstance(gateway.summarizer, MemorySummarizer)
    assert isinstance(gateway.interviewer, Interviewer)
    assert isinstance(gateway.evaluator, ClaimEvaluator)
    assert isinstance(gateway.embeddings, EmbeddingProvider)
    assert isinstance(gateway.token_counter, FixtureTokenCounter)
    assert isinstance(gateway.embeddings, FixtureEmbeddingProvider)


def test_fixture_mode_counts_with_the_heuristic_and_says_which_one():
    gateway = build_gateway(GatewaySettings.from_env(env={}))

    assert gateway.token_counter.version == "heuristic-v1"
    assert gateway.token_counter.model_id == FIXTURE_MODEL_ID


def test_bedrock_mode_counts_with_the_writer_model():
    """The budget is the writer's context, so it is counted the writer's way."""
    gateway = build_gateway(
        GatewaySettings.from_env(env=BEDROCK_ENV), client=FakeRuntime(token_counts=[1])
    )

    assert isinstance(gateway.token_counter, BedrockTokenCounter)
    assert gateway.token_counter.version == BEDROCK_COUNTER_VERSION
    assert gateway.token_counter.model_id == "writer-model"
    assert isinstance(gateway.embeddings, BedrockEmbeddingProvider)
    assert gateway.embeddings.model_id == "embedding-model"
    assert gateway.simulated is False


def test_bedrock_mode_builds_a_strands_invoker_bound_to_the_region():
    gateway = build_gateway(
        GatewaySettings.from_env(env=BEDROCK_ENV), client=FakeRuntime(token_counts=[1])
    )

    writer = caller_of(gateway.writer)
    assert isinstance(writer.invoker, StrandsInvoker)
    assert writer.invoker.region == "eu-west-2"
    assert writer.region == "eu-west-2"
    assert writer.model_id == "writer-model"
    assert writer.simulated is False


def test_each_role_is_wired_to_its_configured_model():
    gateway = build_gateway(
        GatewaySettings.from_env(env=BEDROCK_ENV), client=FakeRuntime(token_counts=[1])
    )

    assert caller_of(gateway.auditor).model_id == "auditor-model"
    assert caller_of(gateway.summarizer).model_id == "summary-model"
    assert caller_of(gateway.evaluator).model_id == "judge-model"
    # The interview is the same agent answering questions, not a different subject.
    assert caller_of(gateway.interviewer).model_id == "writer-model"


def test_a_production_runtime_is_never_given_a_fixture_gateway():
    with pytest.raises(ConfigurationError, match="production runtime"):
        build_gateway(
            GatewaySettings.from_env(env={}),
            runtime=RuntimeSettings.from_env(
                env={"AS_RUNTIME_MODE": "production", **dict.fromkeys(_PRODUCTION_KEYS, "x")}
            ),
        )


def test_a_production_runtime_may_hold_a_bedrock_gateway():
    gateway = build_gateway(
        GatewaySettings.from_env(env=BEDROCK_ENV),
        runtime=RuntimeSettings.from_env(
            env={"AS_RUNTIME_MODE": "production", **dict.fromkeys(_PRODUCTION_KEYS, "x")}
        ),
        client=FakeRuntime(token_counts=[1]),
    )

    assert gateway.simulated is False
    assert RuntimeMode.PRODUCTION.value == "production"


def test_the_retry_budget_comes_from_configuration():
    gateway = build_gateway(GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": "5"}))

    assert caller_of(gateway.writer).retrier.policy.max_attempts == 6


def test_the_prompt_library_is_shared_by_every_role():
    gateway = build_gateway(GatewaySettings.from_env(env={}))

    assert isinstance(gateway.writer, StructuredThoughtWriter)
    assert gateway.writer.prompts is gateway.prompts
    assert isinstance(gateway.evaluator, ClaimEvaluator)


_PRODUCTION_KEYS = (
    "AWS_REGION",
    "WRITER_MODEL_ID",
    "AUDITOR_MODEL_ID",
    "JUDGE_MODEL_ID",
    "SUMMARY_MODEL_ID",
    "EMBEDDING_MODEL_ID",
)


def test_bedrock_mode_builds_its_own_client_from_the_default_credential_chain():
    """Constructing a client needs no credentials; using one does. Nothing is called here."""
    gateway = build_gateway(GatewaySettings.from_env(env=BEDROCK_ENV))

    assert isinstance(gateway.token_counter, BedrockTokenCounter)
    assert isinstance(gateway.embeddings, BedrockEmbeddingProvider)
    assert gateway.token_counter.client is gateway.embeddings.client
