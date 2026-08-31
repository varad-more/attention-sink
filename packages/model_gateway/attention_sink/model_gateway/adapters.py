"""The role adapters, and the one class that knows what a model provider is.

Everything above :class:`StructuredInvoker` -- prompt rendering, the blindness
guard, retries, response verification, metadata -- is provider-agnostic, and the five
role adapters here are that logic and nothing else. Below it sits exactly one
Bedrock-specific class, :class:`StrandsInvoker`. Local fixture mode substitutes a
different invoker and runs the same adapters, so the path a contributor exercises
without an AWS account is the path production takes.

Strands earns its place for one thing: turning a Pydantic model into a structured
request and a validated response. The prompts, the memory state, the retries, the
metadata, and what is persisted all stay in this repository's hands. The SDK is a
calling convention, not the experiment.

Two rules in :class:`StrandsInvoker` are load-bearing, and both are ADR-006 in
practice. The model identifier is always passed explicitly, because the SDK will
otherwise resolve a default that can change underneath a run. And every call builds a
new agent with no conversation manager, so nothing an arm generated in one cycle can
reach the next except through the memory state the policy decided.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from botocore.config import Config as BotocoreConfig
from pydantic import BaseModel
from strands import Agent
from strands.agent.agent_result import AgentResult
from strands.agent.conversation_manager import NullConversationManager
from strands.models import BedrockModel
from strands.types.exceptions import StructuredOutputException

from attention_sink.domain import CompressionPlan, Memory
from attention_sink.model_gateway.failures import (
    ModelInvocationError,
    Retrier,
    RetriesExhausted,
    SchemaRepairNeeded,
)
from attention_sink.model_gateway.interfaces import (
    DEFAULT_ACCEPTED_LEVELS,
    AuditedCitationRecord,
    AuditResult,
    EvaluationJudgment,
    ExactTokenCounter,
    InterviewResult,
    SummaryResult,
    WriterResult,
)
from attention_sink.model_gateway.observability import (
    CallMetadata,
    CallOutcome,
    ModelErrorCode,
    ModelRole,
)
from attention_sink.model_gateway.prompts import (
    DEFAULT_PROMPT_VERSION,
    PromptLibrary,
    PromptName,
)
from attention_sink.model_gateway.rendering import (
    ModelRequest,
    assert_policy_blind,
    build_auditor_request,
    build_evaluation_request,
    build_interview_request,
    build_summarizer_request,
    build_writer_request,
)
from attention_sink.model_gateway.schemas import (
    EVALUATION_CALCULATION_VERSION,
    AuditOutput,
    ClaimedCitation,
    EvaluationOutput,
    EvaluationTask,
    InterviewOutput,
    InterviewQuestion,
    SummaryOutput,
    SupportLevel,
    ThoughtOutput,
)
from attention_sink.model_gateway.settings import WriterInference

__all__ = [
    "RawResponse",
    "StrandsInvoker",
    "StructuredCaller",
    "StructuredCitationAuditor",
    "StructuredClaimEvaluator",
    "StructuredInterviewer",
    "StructuredInvoker",
    "StructuredMemorySummarizer",
    "StructuredThoughtWriter",
]


@dataclass(frozen=True, slots=True)
class RawResponse[T: BaseModel]:
    """One provider response, reduced to what the record needs."""

    output: T
    stop_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    request_id: str | None = None


class StructuredInvoker(Protocol):
    """The single seam between this package and a model provider.

    Narrow on purpose. Everything above it -- prompts, retries, validation,
    metadata -- is testable without a provider, and everything below it is one small
    class whose only job is to speak to one.
    """

    def invoke[T: BaseModel](
        self,
        *,
        model_id: str,
        system: str,
        user: str,
        output_model: type[T],
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> RawResponse[T]:
        """Send one request and return the validated structured response."""


@dataclass(frozen=True, slots=True)
class StrandsInvoker:
    """Invokes Bedrock through a fresh, stateless Strands agent."""

    region: str
    request_timeout_seconds: int = 60

    def invoke[T: BaseModel](
        self,
        *,
        model_id: str,
        system: str,
        user: str,
        output_model: type[T],
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> RawResponse[T]:
        """Build an agent for this one call, run it, and discard it.

        The three lines here are the only ones in this package that reach a provider,
        which is why what surrounds them -- construction and unpacking -- is split out
        into pieces that can be checked without one.

        Raises:
            StructuredOutputException: The response carried no value of the
                requested type.
        """
        agent = self.build_agent(
            model_id=model_id,
            system=system,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )
        result = agent(user, structured_output_model=output_model)
        return unpack_response(result, output_model=output_model, model_id=model_id)

    def build_agent(
        self, *, model_id: str, system: str, temperature: float, top_p: float, max_tokens: int
    ) -> Agent:
        """Construct a stateless agent bound to one explicitly named model.

        Two things here are load-bearing. ``model_id`` is always passed, because the
        SDK otherwise resolves a Region-dependent default that can change underneath
        a run. And the agent starts with no messages and a null conversation manager,
        so nothing an arm generated earlier can reach this call except through the
        memory state the policy decided.

        Botocore's own retries are disabled. This package has a retry policy that
        records what it did; a second, silent one underneath would double every
        backoff and make the recorded retry count a fiction.
        """
        model = BedrockModel(
            model_id=model_id,
            region_name=self.region,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            streaming=False,
            boto_client_config=BotocoreConfig(
                read_timeout=self.request_timeout_seconds,
                connect_timeout=self.request_timeout_seconds,
                retries={"max_attempts": 1, "mode": "standard"},
            ),
        )
        return Agent(
            model=model,
            system_prompt=system,
            messages=[],
            callback_handler=None,
            conversation_manager=NullConversationManager(),
        )


def unpack_response[T: BaseModel](
    result: AgentResult, *, output_model: type[T], model_id: str
) -> RawResponse[T]:
    """Reduce an SDK result to the fields this package records.

    Raises:
        StructuredOutputException: The result carried no value of the requested type,
            which is a malformed response rather than a transport failure.
    """
    output = result.structured_output
    if not isinstance(output, output_model):
        msg = f"{model_id} returned no {output_model.__name__}"
        raise StructuredOutputException(msg)
    usage = result.metrics.accumulated_usage
    return RawResponse(
        output=output,
        stop_reason=str(result.stop_reason),
        input_tokens=usage.get("inputTokens"),
        output_tokens=usage.get("outputTokens"),
    )


@dataclass(frozen=True, slots=True)
class StructuredCaller:
    """Runs one role's calls: retry, verify, and record, around an invoker.

    Every adapter below is a thin layer over this. Sharing it is what makes the
    retry policy, the error classification, and the metadata identical across roles
    rather than five near-copies that drift.
    """

    role: ModelRole
    model_id: str
    region: str
    invoker: StructuredInvoker
    retrier: Retrier = field(default_factory=Retrier)
    clock: Callable[[], float] = time.monotonic
    simulated: bool = False
    """Recorded on every call this caller makes.

    Carried rather than inferred from the invoker's type, so a simulated response
    that is copied into an API result carries the marking with it.
    """

    def call[T: BaseModel](
        self,
        *,
        request: ModelRequest,
        output_model: type[T],
        temperature: float,
        top_p: float,
        max_tokens: int,
        verify: Callable[[T], None] | None = None,
    ) -> tuple[T, CallMetadata]:
        """Invoke until the response is valid, verified, and worth recording.

        Args:
            request: The rendered prompt, already checked for policy vocabulary.
            output_model: The schema the response must fit.
            temperature: Decoding temperature for this role.
            top_p: Nucleus sampling parameter for this role.
            max_tokens: Ceiling on the response.
            verify: An extra check the schema cannot express, such as "every label
                you cited was one you were given". Raises
                :class:`SchemaRepairNeeded` to earn another attempt with a hint.

        Returns:
            The validated output and the metadata for the call that produced it.

        Raises:
            ModelInvocationError: Every permitted attempt failed. The metadata on
                the exception carries the terminal code and the retry count.
        """
        started = self.clock()

        def attempt(_index: int, hint: str | None) -> RawResponse[T]:
            user = request.user if hint is None else f"{request.user}\n\n{hint}"
            assert_policy_blind(user, where=f"{self.role.value} data turn")
            response = self.invoker.invoke(
                model_id=self.model_id,
                system=request.system,
                user=user,
                output_model=output_model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
            if verify is not None:
                verify(response.output)
            return response

        try:
            response, retries = self.retrier.run(attempt)
        except RetriesExhausted as exc:
            metadata = self._metadata(
                request=request,
                outcome=CallOutcome.FAILURE,
                latency_ms=self._elapsed_ms(started),
                retry_count=max(exc.attempts - 1, 0),
                error_code=exc.code,
            )
            msg = f"{self.role.value} call failed as {exc.code.value} using {self.model_id}"
            raise ModelInvocationError(msg, metadata=metadata) from exc

        metadata = self._metadata(
            request=request,
            outcome=CallOutcome.SUCCESS,
            latency_ms=self._elapsed_ms(started),
            retry_count=retries,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            request_id=response.request_id,
            stop_reason=response.stop_reason,
        )
        return response.output, metadata

    def _elapsed_ms(self, started: float) -> int:
        # A fixture answers out of a file: there is no call to time, and the few
        # microseconds of scheduling noise around one are not a property of the
        # cycle. They used to land in `latency_ms`, which is inside the snapshot
        # digest, so on a loaded machine two identical runs sealed different
        # hashes. Zero here is what the fixture embedding provider already
        # records for the same reason.
        if self.simulated:
            return 0
        return max(int((self.clock() - started) * 1000), 0)

    def _metadata(
        self,
        *,
        request: ModelRequest,
        outcome: CallOutcome,
        latency_ms: int,
        retry_count: int,
        error_code: ModelErrorCode | None = None,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        request_id: str | None = None,
        stop_reason: str | None = None,
    ) -> CallMetadata:
        return CallMetadata(
            role=self.role,
            model_id=self.model_id,
            region=self.region,
            outcome=outcome,
            latency_ms=latency_ms,
            retry_count=retry_count,
            simulated=self.simulated,
            request_id=request_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            prompt_version=request.prompt.identifier,
            prompt_hash=request.prompt_hash,
            stop_reason=stop_reason,
            error_code=error_code,
        )


def _unknown_refs(offered: Sequence[str], cited: Sequence[str]) -> tuple[str, ...]:
    """Labels the model used that it was never given."""
    known = set(offered)
    return tuple(dict.fromkeys(ref for ref in cited if ref not in known))


def _cite_only(offered: Sequence[str], unknown: Sequence[str]) -> SchemaRepairNeeded:
    """The repair for a response that invented a label."""
    available = ", ".join(offered) or "none"
    return SchemaRepairNeeded(
        f"You referred to {', '.join(unknown)}, which was not supplied. "
        f"Use only these labels: {available}. Answer again.",
        code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
    )


def _normalise(text: str) -> str:
    """Casefold and collapse whitespace, for comparing a quote to its source."""
    return " ".join(text.split()).casefold()


@dataclass(frozen=True, slots=True)
class StructuredThoughtWriter:
    """Generates one arm's thought for one cycle."""

    prompts: PromptLibrary
    caller: StructuredCaller
    inference: WriterInference
    prompt_version: str = DEFAULT_PROMPT_VERSION

    def write(
        self, *, cycle: int, stimulus_text: str, active_memories: Sequence[Memory]
    ) -> WriterResult:
        """Write the cycle, citing only memories that were actually supplied.

        Raises:
            ModelInvocationError: The response kept citing labels it was not given,
                or the call failed for a provider reason, after every attempt.
        """
        request = build_writer_request(
            self.prompts,
            cycle=cycle,
            stimulus_text=stimulus_text,
            active_memories=active_memories,
            version=self.prompt_version,
        )
        offered = request.presentation.refs

        def verify(output: ThoughtOutput) -> None:
            unknown = _unknown_refs(offered, [c.memory_ref for c in output.claimed_citations])
            if unknown:
                raise _cite_only(offered, unknown)

        output, metadata = self.caller.call(
            request=request,
            output_model=ThoughtOutput,
            temperature=self.inference.temperature,
            top_p=self.inference.top_p,
            max_tokens=self.inference.writer_max_tokens,
            verify=verify,
        )
        return WriterResult(
            output=output,
            cited_memory_ids=request.presentation.resolve_all(
                c.memory_ref for c in output.claimed_citations
            ),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class StructuredCitationAuditor:
    """Checks that a thought rests on the memories it claimed.

    The auditor's quoted evidence is verified against the memory it came from. An
    audit whose supporting span is not in the record it cites is not evidence, and
    accepting one would let a fabricated quotation move a policy statistic -- the
    exact circularity the claim/verification split exists to prevent.
    """

    prompts: PromptLibrary
    caller: StructuredCaller
    inference: WriterInference
    accepted_levels: frozenset[SupportLevel] = DEFAULT_ACCEPTED_LEVELS
    prompt_version: str = DEFAULT_PROMPT_VERSION

    def audit(
        self,
        *,
        journal_entry: str,
        candidate_memory: str,
        claims: Sequence[ClaimedCitation],
        active_memories: Sequence[Memory],
    ) -> AuditResult:
        """Judge every claimed citation and list what nothing supports.

        Raises:
            ModelInvocationError: The audit did not answer every claim, cited an
                unknown label, or quoted evidence absent from the memory it named,
                after every permitted attempt.
        """
        request = build_auditor_request(
            self.prompts,
            journal_entry=journal_entry,
            candidate_memory=candidate_memory,
            claims=claims,
            active_memories=active_memories,
            version=self.prompt_version,
        )
        presentation = request.presentation
        claimed_refs = [claim.memory_ref for claim in claims]

        def verify(output: AuditOutput) -> None:
            audited_refs = [citation.memory_ref for citation in output.audited_citations]
            unknown = _unknown_refs(presentation.refs, audited_refs)
            if unknown:
                raise _cite_only(presentation.refs, unknown)
            if audited_refs != claimed_refs:
                raise SchemaRepairNeeded(
                    "Return one audited_citations entry per claimed citation, in the same "
                    f"order. The claims were: {', '.join(claimed_refs) or 'none'}.",
                    code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
                )
            for citation in output.audited_citations:
                if citation.support_level is SupportLevel.NONE:
                    continue
                source = _normalise(presentation.text_for(citation.memory_ref))
                if _normalise(citation.memory_evidence_span) not in source:
                    raise SchemaRepairNeeded(
                        f"The span you quoted for {citation.memory_ref} is not in that "
                        "record. Quote it verbatim, or mark the citation NONE.",
                        code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
                    )

        output, metadata = self.caller.call(
            request=request,
            output_model=AuditOutput,
            temperature=self.inference.temperature,
            top_p=self.inference.top_p,
            max_tokens=self.inference.writer_max_tokens,
            verify=verify,
        )
        return AuditResult(
            output=output,
            citations=tuple(
                AuditedCitationRecord(
                    memory_id=presentation.resolve(citation.memory_ref),
                    support_level=citation.support_level,
                    memory_evidence_span=citation.memory_evidence_span,
                    entry_evidence_span=citation.entry_evidence_span,
                )
                for citation in output.audited_citations
            ),
            accepted_levels=self.accepted_levels,
            auditor_version=request.prompt.version_token,
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class StructuredMemorySummarizer:
    """Writes the text for a compression the policy already decided.

    The token count that decides whether a summary fits is taken with the same
    counter the budget is denominated in, after generation. A summary the model
    believed was short enough is not a summary that fits.
    """

    prompts: PromptLibrary
    caller: StructuredCaller
    inference: WriterInference
    counter: ExactTokenCounter
    prompt_version: str = DEFAULT_PROMPT_VERSION

    def summarize(self, *, plan: CompressionPlan, sources: Sequence[Memory]) -> SummaryResult:
        """Compress exactly the plan's sources into one record within its ceiling.

        Raises:
            ValueError: ``sources`` are not exactly the plan's sources, in order.
            ModelInvocationError: The summary stayed over the ceiling, or kept
                naming other sources, after every permitted attempt.
        """
        supplied = tuple(memory.memory_id for memory in sources)
        if supplied != plan.source_memory_ids:
            msg = (
                f"summarizer was given {supplied}, but the plan compresses {plan.source_memory_ids}"
            )
            raise ValueError(msg)

        request = build_summarizer_request(
            self.prompts,
            sources=sources,
            summary_token_limit=plan.summary_target_token_limit,
            version=self.prompt_version,
        )
        presentation = request.presentation
        limit = plan.summary_target_token_limit

        def verify(output: SummaryOutput) -> None:
            unknown = _unknown_refs(presentation.refs, output.source_memory_refs)
            if unknown:
                raise _cite_only(presentation.refs, unknown)
            if tuple(output.source_memory_refs) != presentation.refs:
                raise SchemaRepairNeeded(
                    "source_memory_refs must be exactly "
                    f"{', '.join(presentation.refs)}, in that order.",
                    code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
                )
            tokens = self.counter.count(output.summary_text)
            if tokens > limit:
                raise SchemaRepairNeeded(
                    f"Your summary was {tokens} tokens and the limit is {limit}. "
                    "Write a shorter one that keeps the most important facts.",
                    code=ModelErrorCode.TOKEN_LIMIT_EXCEEDED,
                )

        output, metadata = self.caller.call(
            request=request,
            output_model=SummaryOutput,
            temperature=self.inference.temperature,
            top_p=self.inference.top_p,
            max_tokens=self.inference.summary_max_tokens,
            verify=verify,
        )
        return SummaryResult(
            output=output,
            source_memory_ids=plan.source_memory_ids,
            summary_tokens=self.counter.count(output.summary_text),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class StructuredInterviewer:
    """Asks the fixed question set against what an arm currently holds."""

    prompts: PromptLibrary
    caller: StructuredCaller
    inference: WriterInference
    prompt_version: str = DEFAULT_PROMPT_VERSION

    def interview(
        self,
        *,
        questions: Sequence[InterviewQuestion],
        active_memories: Sequence[Memory],
        stimulus_text: str | None = None,
    ) -> InterviewResult:
        """Answer every question from active memory alone.

        Raises:
            ValueError: ``questions`` is empty, or repeats a question identifier.
            ModelInvocationError: The answers did not cover the questions, or cited
                an unknown label, after every permitted attempt.
        """
        asked = [question.question_id for question in questions]
        if not asked:
            msg = "an interview needs at least one question"
            raise ValueError(msg)
        if len(set(asked)) != len(asked):
            msg = f"interview questions repeat an identifier: {asked}"
            raise ValueError(msg)

        request = build_interview_request(
            self.prompts,
            questions=questions,
            active_memories=active_memories,
            stimulus_text=stimulus_text,
            version=self.prompt_version,
        )
        presentation = request.presentation

        def verify(output: InterviewOutput) -> None:
            cited = [ref for answer in output.answers for ref in answer.cited_memory_refs]
            unknown = _unknown_refs(presentation.refs, cited)
            if unknown:
                raise _cite_only(presentation.refs, unknown)
            answered = [answer.question_id for answer in output.answers]
            if answered != asked:
                raise SchemaRepairNeeded(
                    f"Answer each question once, in order: {', '.join(asked)}.",
                    code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
                )

        output, metadata = self.caller.call(
            request=request,
            output_model=InterviewOutput,
            temperature=self.inference.temperature,
            top_p=self.inference.top_p,
            max_tokens=self.inference.writer_max_tokens,
            verify=verify,
        )
        cited_refs = dict.fromkeys(
            ref for answer in output.answers for ref in answer.cited_memory_refs
        )
        return InterviewResult(
            output=output,
            cited_memory_ids=presentation.resolve_all(cited_refs),
            metadata=metadata,
        )


@dataclass(frozen=True, slots=True)
class StructuredClaimEvaluator:
    """Scores text against reference statements, blind to how it was produced."""

    prompts: PromptLibrary
    caller: StructuredCaller
    inference: WriterInference
    prompt_version: str = DEFAULT_PROMPT_VERSION

    def evaluate(
        self,
        *,
        task: EvaluationTask,
        passage: str,
        reference_statements: Sequence[str],
        records: Sequence[Memory] = (),
    ) -> EvaluationJudgment:
        """Return one verdict, one score, and the evidence for both.

        Raises:
            ModelInvocationError: The judgement answered a different task, used an
                unknown label, or fell outside the task's vocabulary, after every
                permitted attempt.
        """
        request = build_evaluation_request(
            self.prompts,
            task=task,
            passage=passage,
            reference_statements=reference_statements,
            records=records,
            version=self.prompt_version,
        )
        presentation = request.presentation

        def verify(output: EvaluationOutput) -> None:
            if output.task is not task:
                raise SchemaRepairNeeded(
                    f"Answer the task you were given, {task.value}.",
                    code=ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
                )
            unknown = _unknown_refs(presentation.refs, output.evidence_memory_refs)
            if unknown:
                raise _cite_only(presentation.refs, unknown)

        output, metadata = self.caller.call(
            request=request,
            output_model=EvaluationOutput,
            temperature=self.inference.temperature,
            top_p=self.inference.top_p,
            max_tokens=self.inference.writer_max_tokens,
            verify=verify,
        )
        template = self.prompts.load(
            PromptName.SUMMARY_ENTAILMENT
            if task is EvaluationTask.SUMMARY_ENTAILMENT
            else PromptName.TRUTH_EVALUATOR,
            self.prompt_version,
        )
        return EvaluationJudgment(
            output=output,
            evidence_memory_ids=presentation.resolve_all(output.evidence_memory_refs),
            evaluator_model_id=self.caller.model_id,
            prompt_version=template.version_token,
            calculation_version=EVALUATION_CALCULATION_VERSION,
            metadata=metadata,
        )
