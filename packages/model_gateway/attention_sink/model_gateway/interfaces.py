"""The contracts every model interaction goes through, and what each one returns.

Seven roles, seven protocols. Nothing in this system talks to a model except through
one of them, which is what makes "which model produced this, under which prompt, at
what cost" answerable for every generated value in a run.

Each result pairs the validated output with the real memory identifiers behind the
labels the model used, and with the metadata for the call. The resolution happens
here, once, rather than at each call site: a caller that had to map labels back
itself could map them back wrongly, and a citation attributed to the wrong memory
would move a policy statistic in the wrong direction.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from attention_sink.domain import (
    ArmId,
    CitationSource,
    CompressionPlan,
    Memory,
    MemoryId,
    MetricEvidence,
    UtcTimestamp,
    VerifiedCitation,
)
from attention_sink.model_gateway.observability import CallMetadata
from attention_sink.model_gateway.schemas import (
    AuditOutput,
    ClaimedCitation,
    EmbeddingRecord,
    EvaluationOutput,
    EvaluationTask,
    InterviewOutput,
    InterviewQuestion,
    SummaryOutput,
    SupportLevel,
    ThoughtOutput,
    UnsupportedClaim,
)

if TYPE_CHECKING:  # pragma: no cover - imports exist for typing only
    from mypy_boto3_bedrock_runtime.type_defs import CountTokensInputTypeDef

__all__ = [
    "AuditResult",
    "AuditedCitationRecord",
    "BedrockRuntimeApi",
    "CitationAuditor",
    "ClaimEvaluator",
    "EmbeddingProvider",
    "EmbeddingResult",
    "EvaluationJudgment",
    "ExactTokenCounter",
    "InterviewResult",
    "Interviewer",
    "MemorySummarizer",
    "SummaryResult",
    "ThoughtWriter",
    "TokenCount",
    "WriterResult",
]

DEFAULT_ACCEPTED_LEVELS: frozenset[SupportLevel] = frozenset({SupportLevel.FULL})
"""Support levels that may move a policy statistic.

``PARTIAL`` is excluded by default and included only where a run configures it. A
partially supported citation is real evidence about the writing and no evidence that
the memory was load-bearing, so counting it by default would inflate exactly the
signal the citation-weighted arm is built on.
"""


# --------------------------------------------------------------------- results


@dataclass(frozen=True, slots=True)
class WriterResult:
    """One cycle of writing, with its claimed citations resolved."""

    output: ThoughtOutput
    cited_memory_ids: tuple[MemoryId, ...]
    """Real identifiers behind the claimed labels, in claim order. Unverified."""

    metadata: CallMetadata


@dataclass(frozen=True, slots=True)
class AuditedCitationRecord:
    """One claimed citation after audit, against a real memory."""

    memory_id: MemoryId
    support_level: SupportLevel
    memory_evidence_span: str
    entry_evidence_span: str


@dataclass(frozen=True, slots=True)
class AuditResult:
    """What an auditor concluded about one piece of writing."""

    output: AuditOutput
    citations: tuple[AuditedCitationRecord, ...]
    accepted_levels: frozenset[SupportLevel]
    auditor_version: str
    """The prompt this audit was made under, as the domain's ``Version`` accepts it."""

    metadata: CallMetadata

    @property
    def verified(self) -> tuple[AuditedCitationRecord, ...]:
        """Citations at a support level this run counts."""
        return tuple(c for c in self.citations if c.support_level in self.accepted_levels)

    @property
    def rejected(self) -> tuple[AuditedCitationRecord, ...]:
        """Citations the writer claimed that the audit did not sustain.

        Kept rather than discarded: how often an arm claims memories it did not use
        is a finding about that arm, not noise.
        """
        return tuple(c for c in self.citations if c.support_level not in self.accepted_levels)

    @property
    def unsupported_claims(self) -> tuple[UnsupportedClaim, ...]:
        """Assertions in the entry that rest on no supplied memory."""
        return tuple(self.output.unsupported_claims)

    @property
    def state_updating_memory_ids(self) -> tuple[MemoryId, ...]:
        """Memories whose statistics this audit may move, each once, in order."""
        seen: dict[MemoryId, None] = {}
        for citation in self.verified:
            seen.setdefault(citation.memory_id, None)
        return tuple(seen)

    def as_verified_citations(
        self,
        *,
        run_id: str,
        arm_id: ArmId,
        cycle: int,
        source: CitationSource = CitationSource.WRITER,
    ) -> tuple[VerifiedCitation, ...]:
        """Render the sustained citations as the records a policy reads.

        Only citations at an accepted support level are rendered, so the filter that
        decides what may move a statistic lives here rather than in each caller. The
        evidence is the two spans the audit quoted, joined deterministically: a
        stored justification is never a second generation.
        """
        return tuple(
            VerifiedCitation(
                run_id=run_id,
                arm_id=arm_id,
                cycle=cycle,
                memory_id=citation.memory_id,
                source=source,
                auditor_version=self.auditor_version,
                evidence=(
                    f"memory: {citation.memory_evidence_span} | "
                    f"entry: {citation.entry_evidence_span}"
                ),
            )
            for citation in self.verified
        )


@dataclass(frozen=True, slots=True)
class SummaryResult:
    """One summary written for a plan the policy had already fixed."""

    output: SummaryOutput
    source_memory_ids: tuple[MemoryId, ...]
    """Exactly the plan's sources. Checked before this record exists."""

    summary_tokens: int
    metadata: CallMetadata


@dataclass(frozen=True, slots=True)
class InterviewResult:
    """Answers to the fixed question set.

    Never admitted to memory and never able to move a citation statistic. The
    identifiers here exist for analysis: an arm that can still answer from a memory
    is a finding, and it must not also be an intervention.
    """

    output: InterviewOutput
    cited_memory_ids: tuple[MemoryId, ...]
    metadata: CallMetadata


@dataclass(frozen=True, slots=True)
class EvaluationJudgment:
    """One structured judgement, with everything needed to dispute it."""

    output: EvaluationOutput
    evidence_memory_ids: tuple[MemoryId, ...]
    evaluator_model_id: str
    prompt_version: str
    calculation_version: str
    metadata: CallMetadata

    def as_metric_evidence(
        self,
        *,
        run_id: str,
        arm_id: ArmId,
        cycle: int,
        metric_name: str,
        computed_at: UtcTimestamp,
    ) -> MetricEvidence:
        """Render this judgement as the domain's storable evidence record.

        The rationale is assembled deterministically from the verdict and the
        excerpts the judge quoted. It is never a second generation: a stored
        justification that no longer matches the score it justifies is worse than
        none at all.
        """
        excerpts = " | ".join(self.output.supporting_excerpts) or "no excerpt supplied"
        return MetricEvidence(
            run_id=run_id,
            arm_id=arm_id,
            cycle=cycle,
            metric_name=metric_name,
            value=self.output.score,
            evaluator_version=self.prompt_version,
            calculation_version=self.calculation_version,
            cited_memory_ids=self.evidence_memory_ids,
            rationale=f"{self.output.task.value}={self.output.label}; {excerpts}",
            computed_at=computed_at,
        )


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """One embedding, and whether it had to be computed at all."""

    record: EmbeddingRecord
    deduplicated: bool
    """True when the model identifier and content hash were already known."""

    metadata: CallMetadata


@dataclass(frozen=True, slots=True)
class TokenCount:
    """An exact token count and the call that produced it."""

    tokens: int
    metadata: CallMetadata


# ------------------------------------------------------------------- protocols


@runtime_checkable
class ThoughtWriter(Protocol):
    """Produces one arm's thought for one cycle.

    Sees the cycle number, this cycle's stimulus, and the active memories. Never the
    arm, the policy, another arm, a later stimulus, a metric, or a retired memory.
    """

    def write(
        self, *, cycle: int, stimulus_text: str, active_memories: Sequence[Memory]
    ) -> WriterResult:
        """Generate a journal entry, a candidate memory, and citation claims.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
            PromptLeakError: A retired memory or banned vocabulary reached the prompt.
            UnknownMemoryReferenceError: The response cited a label it was not given.
        """


@runtime_checkable
class CitationAuditor(Protocol):
    """Decides whether a thought actually rests on the memories it claimed."""

    def audit(
        self,
        *,
        journal_entry: str,
        candidate_memory: str,
        claims: Sequence[ClaimedCitation],
        active_memories: Sequence[Memory],
    ) -> AuditResult:
        """Judge each claimed citation and list what nothing supports.

        Args:
            journal_entry: The writing under audit.
            candidate_memory: The sentence the writer proposed keeping.
            claims: The writer's :class:`ClaimedCitation` values, as it made them.
            active_memories: The memories the writer was given, in the same order.

        Raises:
            ModelInvocationError: Every permitted attempt failed, including because
                the audit quoted evidence that is not in the memory it cited.
        """


@runtime_checkable
class MemorySummarizer(Protocol):
    """Writes the text for a compression the policy has already decided."""

    def summarize(self, *, plan: CompressionPlan, sources: Sequence[Memory]) -> SummaryResult:
        """Compress exactly the plan's sources into one record within its ceiling.

        Raises:
            ModelInvocationError: The summary stayed over the ceiling, or named
                sources other than the plan's, after every permitted attempt.
        """


@runtime_checkable
class Interviewer(Protocol):
    """Asks the fixed question set against what an arm currently holds."""

    def interview(
        self,
        *,
        questions: Sequence[InterviewQuestion],
        active_memories: Sequence[Memory],
        stimulus_text: str | None = None,
    ) -> InterviewResult:
        """Answer every question from active memory alone.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """


@runtime_checkable
class ClaimEvaluator(Protocol):
    """Scores text against reference statements, blind to how it was produced."""

    def evaluate(
        self,
        *,
        task: EvaluationTask,
        passage: str,
        reference_statements: Sequence[str],
        records: Sequence[Memory] = (),
    ) -> EvaluationJudgment:
        """Return one categorical verdict, one score, and the evidence for both.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Turns text into a vector, once per model identifier and content hash."""

    def embed(self, text: str) -> EmbeddingResult:
        """Embed ``text``, returning a cached record when one already exists.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """


@runtime_checkable
class ExactTokenCounter(Protocol):
    """Counts tokens the way the model that will read them counts.

    Extends the domain's ``TokenCounter`` with the two counts a budget actually
    needs: a serialised block of active memory, and the complete request that block
    will be sent inside.
    """

    @property
    def version(self) -> str:
        """Counter version, recorded on every ``TokenBudget`` that uses it."""

    @property
    def model_id(self) -> str:
        """The model whose tokenisation this counter reports."""

    def count(self, text: str) -> int:
        """Return the token cost of ``text``."""

    def count_detailed(self, text: str) -> TokenCount:
        """Return the token cost of ``text`` with the metadata for the call."""

    def count_request(self, *, system: str, user: str) -> TokenCount:
        """Return the exact token cost of a complete two-turn request."""


class BedrockRuntimeApi(Protocol):
    """The two provider operations this package calls directly.

    Declared as the surface actually used rather than taken as the whole client, so
    the calls stay checked against the real service model while a test can supply
    something small enough to reason about.
    """

    def count_tokens(self, *, modelId: str, input: CountTokensInputTypeDef) -> Mapping[str, Any]:
        """Return the exact input-token count for a request."""

    def invoke_model(
        self, *, modelId: str, body: str, accept: str, contentType: str
    ) -> Mapping[str, Any]:
        """Invoke a model whose interface is a raw JSON body."""
