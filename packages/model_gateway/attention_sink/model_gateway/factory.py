"""Assembling a gateway from validated configuration, and refusing to when it is wrong.

One function decides what a process talks to, and it decides once. Nothing downstream
chooses between a real model and a fake: an adapter is handed an invoker and does not
know which kind it holds, which is what makes "no production endpoint silently
returns mock data" a property of the wiring rather than a rule people remember.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

import boto3
from botocore.config import Config as BotocoreConfig

from attention_sink.model_gateway.adapters import (
    StrandsInvoker,
    StructuredCaller,
    StructuredCitationAuditor,
    StructuredClaimEvaluator,
    StructuredInterviewer,
    StructuredInvoker,
    StructuredMemorySummarizer,
    StructuredThoughtWriter,
)
from attention_sink.model_gateway.embeddings import BedrockEmbeddingProvider
from attention_sink.model_gateway.failures import Retrier, RetryPolicy
from attention_sink.model_gateway.fixtures import (
    FIXTURE_MODEL_ID,
    FIXTURE_REGION,
    FixtureEmbeddingProvider,
    FixtureInvoker,
    FixtureTokenCounter,
)
from attention_sink.model_gateway.interfaces import (
    DEFAULT_ACCEPTED_LEVELS,
    BedrockRuntimeApi,
    CitationAuditor,
    ClaimEvaluator,
    EmbeddingProvider,
    ExactTokenCounter,
    Interviewer,
    MemorySummarizer,
    ThoughtWriter,
)
from attention_sink.model_gateway.observability import ModelRole
from attention_sink.model_gateway.prompts import DEFAULT_PROMPT_VERSION, PromptLibrary
from attention_sink.model_gateway.schemas import SupportLevel
from attention_sink.model_gateway.settings import (
    ConfigurationError,
    GatewaySettings,
    ModelMode,
    RuntimeMode,
    RuntimeSettings,
)
from attention_sink.model_gateway.tokens import BedrockTokenCounter

__all__ = ["ModelGateway", "build_gateway"]


@dataclass(frozen=True, slots=True)
class ModelGateway:
    """Every model interaction this experiment is allowed to make, in one object."""

    settings: GatewaySettings
    prompts: PromptLibrary
    writer: ThoughtWriter
    auditor: CitationAuditor
    summarizer: MemorySummarizer
    interviewer: Interviewer
    evaluator: ClaimEvaluator
    embeddings: EmbeddingProvider
    token_counter: ExactTokenCounter

    @property
    def simulated(self) -> bool:
        """Whether this gateway fabricates responses instead of invoking a model."""
        return self.settings.is_simulated


def build_gateway(
    settings: GatewaySettings,
    *,
    runtime: RuntimeSettings | None = None,
    prompts: PromptLibrary | None = None,
    invoker: StructuredInvoker | None = None,
    client: BedrockRuntimeApi | None = None,
    sleep: Callable[[float], None] | None = None,
    accepted_levels: frozenset[SupportLevel] = DEFAULT_ACCEPTED_LEVELS,
    embedding_dimensions: int = 1024,
    prompt_version: str = DEFAULT_PROMPT_VERSION,
) -> ModelGateway:
    """Build the gateway ``settings`` describes, or refuse to build one at all.

    Args:
        settings: Resolved gateway configuration. Bedrock mode has already been
            checked for a Region and a full set of model identifiers.
        runtime: The process's runtime mode, when it is known here. A production
            runtime may not be given a fixture gateway. ``GatewaySettings.from_env``
            makes the same check; this one catches settings built by hand.
        prompts: Prompt library. Defaults to the one shipped in this package.
        invoker: Replaces the provider seam. Supplied by tests, and by the opt-in
            contract suite, never by production code.
        client: A ``bedrock-runtime`` client for token counting and embeddings.
            Constructed from the default credential chain when absent.
        sleep: How the retry policy waits. Tests pass a no-op.
        accepted_levels: Support levels that may move a policy statistic.
        embedding_dimensions: Output size for the embedding model.
        prompt_version: Which version of every prompt this gateway uses.

    Raises:
        ConfigurationError: A production runtime asked for a fixture gateway.
    """
    if (
        runtime is not None
        and runtime.mode is RuntimeMode.PRODUCTION
        and settings.mode is ModelMode.FIXTURE
    ):
        msg = (
            "refusing to build a fixture gateway for a production runtime; "
            "fabricated generations must never be served as real ones"
        )
        raise ConfigurationError(msg)

    library = prompts if prompts is not None else PromptLibrary()
    policy = RetryPolicy(max_attempts=settings.max_model_retries + 1)

    def retrier() -> Retrier:
        """A retrier per adapter, so no two roles share a generator."""
        return Retrier(policy=policy) if sleep is None else Retrier(policy=policy, sleep=sleep)

    if settings.mode is ModelMode.FIXTURE:
        return _assemble(
            settings=settings,
            prompts=library,
            invoker=invoker if invoker is not None else FixtureInvoker(),
            region=FIXTURE_REGION,
            models=dict.fromkeys(ModelRole, FIXTURE_MODEL_ID),
            retrier=retrier,
            simulated=True,
            accepted_levels=accepted_levels,
            prompt_version=prompt_version,
            token_counter=FixtureTokenCounter(),
            embeddings=FixtureEmbeddingProvider(dimensions=min(embedding_dimensions, 1024)),
        )

    configured = settings.models
    if configured is None:  # pragma: no cover - forbidden by GatewaySettings
        msg = "bedrock mode reached the factory without a model configuration"
        raise ConfigurationError(msg)

    runtime_client = client if client is not None else _bedrock_client(settings)
    return _assemble(
        settings=settings,
        prompts=library,
        invoker=invoker
        if invoker is not None
        else StrandsInvoker(
            region=configured.region, request_timeout_seconds=settings.request_timeout_seconds
        ),
        region=configured.region,
        models={
            ModelRole.WRITER: configured.writer_model_id,
            ModelRole.AUDITOR: configured.auditor_model_id,
            ModelRole.SUMMARIZER: configured.summary_model_id,
            # The interview is the same agent answering questions, so it is the
            # writer's model. A different one would be a different subject.
            ModelRole.INTERVIEWER: configured.writer_model_id,
            ModelRole.EVALUATOR: configured.judge_model_id,
            ModelRole.EMBEDDING: configured.embedding_model_id,
            # The budget is the writer's context, so it is counted the writer's way.
            ModelRole.TOKEN_COUNTER: configured.writer_model_id,
        },
        retrier=retrier,
        simulated=False,
        accepted_levels=accepted_levels,
        prompt_version=prompt_version,
        token_counter=BedrockTokenCounter(
            model_id=configured.writer_model_id,
            region=configured.region,
            client=runtime_client,
            retrier=retrier(),
        ),
        embeddings=BedrockEmbeddingProvider(
            model_id=configured.embedding_model_id,
            region=configured.region,
            client=runtime_client,
            dimensions=embedding_dimensions,
            retrier=retrier(),
        ),
    )


def _assemble(
    *,
    settings: GatewaySettings,
    prompts: PromptLibrary,
    invoker: StructuredInvoker,
    region: str,
    models: dict[ModelRole, str],
    retrier: Callable[[], Retrier],
    simulated: bool,
    accepted_levels: frozenset[SupportLevel],
    prompt_version: str,
    token_counter: ExactTokenCounter,
    embeddings: EmbeddingProvider,
) -> ModelGateway:
    """Wire five role adapters over one invoker."""

    def caller(role: ModelRole) -> StructuredCaller:
        return StructuredCaller(
            role=role,
            model_id=models[role],
            region=region,
            invoker=invoker,
            retrier=retrier(),
            simulated=simulated,
        )

    inference = settings.inference
    return ModelGateway(
        settings=settings,
        prompts=prompts,
        writer=StructuredThoughtWriter(
            prompts=prompts,
            caller=caller(ModelRole.WRITER),
            inference=inference,
            prompt_version=prompt_version,
        ),
        auditor=StructuredCitationAuditor(
            prompts=prompts,
            caller=caller(ModelRole.AUDITOR),
            inference=inference,
            accepted_levels=accepted_levels,
            prompt_version=prompt_version,
        ),
        summarizer=StructuredMemorySummarizer(
            prompts=prompts,
            caller=caller(ModelRole.SUMMARIZER),
            inference=inference,
            counter=token_counter,
            prompt_version=prompt_version,
        ),
        interviewer=StructuredInterviewer(
            prompts=prompts,
            caller=caller(ModelRole.INTERVIEWER),
            inference=inference,
            prompt_version=prompt_version,
        ),
        evaluator=StructuredClaimEvaluator(
            prompts=prompts,
            caller=caller(ModelRole.EVALUATOR),
            inference=inference,
            prompt_version=prompt_version,
        ),
        embeddings=embeddings,
        token_counter=token_counter,
    )


def _bedrock_client(settings: GatewaySettings) -> BedrockRuntimeApi:
    """Construct a client from the default credential chain, with retries disabled.

    Botocore's own retries are off because this package has a retry policy that
    records what it did. A second, silent one underneath would double every backoff
    and make the recorded retry count a fiction.
    """
    return boto3.Session().client(
        "bedrock-runtime",
        region_name=settings.region,
        config=BotocoreConfig(
            read_timeout=settings.request_timeout_seconds,
            connect_timeout=settings.request_timeout_seconds,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
    )
