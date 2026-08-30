"""What the API is allowed to say, stated as types rather than as care.

Every response is a projection, never a stored record passed through. That is the
mechanism: a field that must not be published cannot leak by being added upstream,
because nothing here copies unknown fields. When Phase 6's frontend and Phase 7's
Lambda read these, they read the same shapes.

The envelope is uniform so a client never has to guess whether it received a list, an
object, or an error, and so pagination can be added to a route later without changing
what the route already returns.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.analysis import GraveyardEntry, export_labels
from attention_sink.domain import ArmId, MemoryState
from attention_sink.pilot import ArmCycleSnapshot
from attention_sink.pilot.repositories import RunRecord, StoredInterview

__all__ = [
    "ApiEnvelope",
    "ArmSummary",
    "CycleView",
    "GraveyardView",
    "InterviewView",
    "MemoryView",
    "Page",
    "RunSummary",
]


class ApiEnvelope[T](BaseModel):
    """One shape for every response, so a client parses one thing.

    ``simulated`` and ``labels`` describe the run the response came from, and are
    said on every response rather than once at the root because a client renders
    responses, not roots. The defaults are the safe direction -- simulated, local,
    non-canonical -- so a route that forgot to name its run under-claims rather than
    over-claims. :meth:`of` is how a run-scoped route fills them in, and every such
    route uses it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    data: T
    simulated: bool = True
    labels: tuple[str, ...] = ("LOCAL_FIXTURE", "NON_CANONICAL", "SIMULATED_MODEL_OUTPUTS")

    @classmethod
    def of(cls, data: T, run: RunRecord | None = None) -> ApiEnvelope[T]:
        """Wrap ``data``, described by the run it came from.

        Derived rather than defaulted, because the first deployed API told every
        reader that a run driven by real Bedrock generations was a local fixture --
        which is the one thing these two fields exist to prevent.
        """
        if run is None:
            return cls(data=data)
        return cls(
            data=data,
            simulated=run.configuration.simulated,
            labels=export_labels(run),
        )


class Page[T](BaseModel):
    """A window onto a longer list, and enough to ask for the next one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    items: list[T]
    total: int = Field(ge=0)
    limit: int = Field(gt=0)
    offset: int = Field(ge=0)

    @classmethod
    def of(cls, items: Sequence[T], *, limit: int, offset: int) -> Page[T]:
        """Slice ``items`` into one page."""
        return cls(
            items=list(items[offset : offset + limit]),
            total=len(items),
            limit=limit,
            offset=offset,
        )


class RunSummary(BaseModel):
    """A run's public head. No configuration secrets, no prompt text."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str
    run_kind: str
    status: str
    current_cycle: int
    maximum_cycles: int
    checkpoint_cycles: tuple[int, ...]
    arms: tuple[str, ...]
    memory_budget_tokens: int
    token_count_source: str
    protocol_version: str
    writer_prompt_version: str
    summary_prompt_version: str
    prompt_set_digest: str
    """Published deliberately. A digest identifies the apparatus without publishing
    it, which is what makes a run reproducible without making a prompt copyable."""

    simulated: bool
    created_at: str
    updated_at: str

    @classmethod
    def of(cls, run: RunRecord) -> RunSummary:
        """Project one run record onto what may be published."""
        configuration = run.configuration
        return cls(
            run_id=run.run_id,
            run_kind=run.run_kind.value,
            status=run.status.value,
            current_cycle=run.current_cycle,
            maximum_cycles=configuration.maximum_cycles,
            checkpoint_cycles=configuration.checkpoint_cycles,
            arms=tuple(arm.value for arm in configuration.arms),
            memory_budget_tokens=configuration.memory_budget_tokens,
            token_count_source=configuration.token_count_source,
            protocol_version=str(configuration.protocol_version),
            writer_prompt_version=str(configuration.writer_prompt_version),
            summary_prompt_version=str(configuration.summary_prompt_version),
            prompt_set_digest=configuration.prompt_set_digest,
            simulated=configuration.simulated,
            created_at=run.created_at.isoformat(),
            updated_at=run.updated_at.isoformat(),
        )


class MemoryView(BaseModel):
    """One active memory, as a reader may see it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    memory_id: str
    text: str
    memory_kind: str
    birth_cycle: int
    token_count: int
    citation_count: int
    pinned: bool


class ArmSummary(BaseModel):
    """One arm's current public state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    arm_id: str
    active_memory_count: int
    active_tokens: int
    budget_tokens: int
    retired_memory_count: int
    state_hash: str
    active_memories: list[MemoryView]

    @classmethod
    def of(cls, arm_id: ArmId, state: MemoryState, run: RunRecord) -> ArmSummary:
        """Project one arm's stored state onto what may be published."""
        return cls(
            arm_id=arm_id.value,
            active_memory_count=len(state.active_memories),
            active_tokens=state.active_tokens,
            budget_tokens=run.configuration.memory_budget_tokens,
            retired_memory_count=len(state.memories) - len(state.active_memories),
            state_hash=state.state_hash,
            active_memories=[
                MemoryView(
                    memory_id=memory.memory_id,
                    text=memory.text,
                    memory_kind=memory.memory_kind.value,
                    birth_cycle=memory.birth_cycle,
                    token_count=memory.token_count,
                    citation_count=memory.citation_count,
                    pinned=memory.pinned,
                )
                for memory in state.active_memories
            ],
        )


class CycleView(BaseModel):
    """One arm's completed cycle.

    Carries the stimulus *text* for this cycle only, and never the deck. A reader who
    could ask for the deck could read the arms' future, which is the one thing an
    observer of a running experiment must not be able to do.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    arm_id: str
    cycle: int
    stimulus_id: str
    stimulus_text: str
    journal_entry: str
    candidate_memory: str
    candidate_memory_id: str
    validated_citation_count: int
    rejected_claim_count: int
    retired_memory_ids: list[str]
    compressed_memory_ids: list[str]
    created_summary_id: str | None
    summary_source_memory_ids: list[str]
    tokens_before: int
    tokens_after: int
    budget_tokens: int
    policy_version: str
    policy_decision_codes: list[str]
    """Why the mechanism did what it did, in its own words.

    Produced by deterministic code, never by a model. Published because the whole
    exhibition rests on a reader being able to check a mechanism's reason against
    what it actually retired."""

    prompt_versions: dict[str, str]
    prompt_hashes: dict[str, str]
    state_hash: str
    snapshot_hash: str
    simulated: bool
    run_kind: str

    @classmethod
    def of(cls, snapshot: ArmCycleSnapshot) -> CycleView:
        """Project one committed snapshot onto what may be published.

        ``stimulus.phase``, ``stimulus.reliability``, and every evaluator note stay
        behind: they say what the stimulus was *for*, and publishing them would hand
        a reader the answer key alongside the answers.
        """
        return cls(
            arm_id=snapshot.arm_id.value,
            cycle=snapshot.cycle,
            stimulus_id=snapshot.stimulus.stimulus_id,
            stimulus_text=snapshot.stimulus.text,
            journal_entry=snapshot.journal_entry,
            candidate_memory=snapshot.candidate_memory,
            candidate_memory_id=snapshot.candidate_memory_id,
            validated_citation_count=len(snapshot.validated_citations),
            rejected_claim_count=len(snapshot.rejected_claims),
            retired_memory_ids=[r.memory_id for r in snapshot.retired_memories],
            compressed_memory_ids=list(snapshot.compressed_memory_ids),
            created_summary_id=(
                None if snapshot.created_summary is None else snapshot.created_summary.memory_id
            ),
            summary_source_memory_ids=list(snapshot.summary_source_memory_ids),
            tokens_before=snapshot.tokens_before,
            tokens_after=snapshot.tokens_after,
            budget_tokens=snapshot.budget_tokens,
            policy_version=str(snapshot.policy_version),
            policy_decision_codes=[
                decision.decision_code.value for decision in snapshot.policy_decisions
            ],
            prompt_versions=dict(snapshot.prompt_versions),
            prompt_hashes=dict(snapshot.prompt_hashes),
            state_hash=snapshot.state_hash,
            snapshot_hash=snapshot.snapshot_hash,
            simulated=snapshot.simulated,
            run_kind=snapshot.run_kind.value,
        )


class GraveyardView(BaseModel):
    """One lost memory, as a reader may see it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    arm_id: str
    memory_id: str
    text: str
    memory_type: str
    birth_cycle: int
    retirement_cycle: int
    lifespan: int
    status: str
    validated_citation_count: int
    last_cited_cycle: int | None
    retirement_reason: str
    policy_version: str
    snapshot_evidence: str
    summary_descendant_id: str | None
    genuinely_inaccessible: bool
    nearest_future_echo_id: str | None

    @classmethod
    def of(cls, entry: GraveyardEntry) -> GraveyardView:
        """Project one Graveyard entry onto what may be published."""
        return cls(
            arm_id=entry.arm_id.value,
            memory_id=entry.memory_id,
            text=entry.text,
            memory_type=entry.memory_type,
            birth_cycle=entry.birth_cycle,
            retirement_cycle=entry.retirement_cycle,
            lifespan=entry.lifespan,
            status=entry.status.value,
            validated_citation_count=entry.validated_citation_count,
            last_cited_cycle=entry.last_cited_cycle,
            retirement_reason=entry.retirement_reason,
            policy_version=entry.policy_version,
            snapshot_evidence=entry.snapshot_evidence,
            summary_descendant_id=entry.summary_descendant_id,
            genuinely_inaccessible=entry.genuinely_inaccessible,
            nearest_future_echo_id=entry.nearest_future_echo_id,
        )


class InterviewView(BaseModel):
    """One stored interview, without the prompt that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    arm_id: str
    cycle: int
    interview_version: str
    question_set_version: str
    answers: list[dict[str, Any]]
    reported_memory_ids: list[str]
    prompt_hash: str
    input_state_hash: str
    record_hash: str
    completed_at: str

    @classmethod
    def of(cls, interview: StoredInterview) -> InterviewView:
        """Project one stored interview onto what may be published."""
        return cls(
            arm_id=interview.arm_id.value,
            cycle=interview.cycle,
            interview_version=str(interview.interview_version),
            question_set_version=str(interview.question_set_version),
            answers=[dict(answer) for answer in interview.answers],
            reported_memory_ids=list(interview.reported_memory_ids),
            prompt_hash=interview.prompt_hash,
            input_state_hash=interview.input_state_hash,
            record_hash=interview.record_hash,
            completed_at=interview.completed_at.isoformat(),
        )
