"""The local pilot engine: six arms, one stimulus, one cycle at a time.

Persistence-independent by construction. The engine holds the run in memory, hands
back immutable snapshots, and never writes a file; `export.py` decides what to do
with what it produced. That separation is what makes the same engine usable from a
test, from a command line, and later from a handler, without any of them being the
place the invariants live.

The cycle is staged before it is committed. All six arms are generated, rebalanced,
and turned into snapshot candidates while the run's state is untouched; only when
every one of the six has succeeded and the cross-arm checks have passed does the run
advance. An arm that fails takes the cycle down with it and leaves all six states
exactly as they were, because five arms that advanced and one that did not are no
longer the same experiment.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from attention_sink.domain import (
    ArmId,
    CitationClaim,
    CitationSource,
    CompressingMemoryPolicy,
    CompressionPlan,
    CycleContext,
    Memory,
    MemoryId,
    MemoryKind,
    MemoryPolicy,
    MemoryState,
    MemoryStatus,
    PolicyDecision,
    UnsatisfiableBudgetError,
    VerifiedCitation,
)
from attention_sink.model_gateway import (
    CallMetadata,
    InterviewQuestion,
    InterviewResult,
    ModelGateway,
    ModelRole,
    WriterResult,
)
from attention_sink.pilot.budget import ModelCallBudget
from attention_sink.pilot.configuration import PilotRunConfiguration
from attention_sink.pilot.protocol import CitationMode, ProtocolBundle, SeedMemorySpec, StimulusSpec
from attention_sink.pilot.snapshots import (
    CLAIMED_VALIDATOR_VERSION,
    ArmCycleSnapshot,
    MemoryStatistic,
    RejectedClaim,
    RetiredMemoryRecord,
    RunSnapshot,
    RunStatus,
    StimulusRecord,
)
from attention_sink.policies import policies_for

__all__ = [
    "ArmGeneration",
    "ArmResult",
    "CheckpointRecord",
    "CycleSequenceError",
    "PilotEngine",
    "RebalanceOutcome",
    "StagedCycle",
    "validate_claims",
]


class CycleSequenceError(RuntimeError):
    """A cycle was requested that is not the one this run is ready to run next."""


def _utc_now() -> datetime:
    """The clock the engine reads. Injectable so a snapshot hash can be pinned."""
    return datetime.now(UTC)


# ---------------------------------------------------------------- citation gate


def validate_claims(
    state: MemoryState, cited_memory_ids: Sequence[MemoryId]
) -> tuple[tuple[MemoryId, ...], tuple[RejectedClaim, ...]]:
    """Split a writer's citation claims into what may count and what may not.

    Three checks, in the order a claim fails them: the memory must exist in this
    arm, it must currently be active, and it must not already have been counted this
    cycle. The last is a normalisation rather than a rejection in spirit -- a writer
    that cites the same memory in two sentences used it once -- but it is recorded as
    a rejection so that the count of claims and the count of statistics that moved
    always add up.

    The state is the authority here rather than the labels the request offered. A
    request presents only active memories, so the ``not_active`` branch cannot fire
    through the writer path today; it fires for a claim replayed against a later
    state, which is exactly when a silently-counted stale citation would be worst.
    """
    accepted: list[MemoryId] = []
    rejected: list[RejectedClaim] = []
    seen: set[MemoryId] = set()
    for memory_id in cited_memory_ids:
        memory = state.get(memory_id)
        if memory is None or not memory.is_active:
            rejected.append(RejectedClaim(memory_id=memory_id, reason="not_active"))
        elif memory_id in seen:
            rejected.append(RejectedClaim(memory_id=memory_id, reason="duplicate"))
        else:
            seen.add(memory_id)
            accepted.append(memory_id)
    return tuple(accepted), tuple(rejected)


# ------------------------------------------------------------------- transfer


@dataclass(frozen=True, slots=True)
class ArmGeneration:
    """What one arm wrote this cycle, and what of it survived validation."""

    arm_id: ArmId
    writer: WriterResult
    claims: tuple[CitationClaim, ...]
    citations: tuple[VerifiedCitation, ...]
    rejected: tuple[RejectedClaim, ...]


@dataclass(frozen=True, slots=True)
class RebalanceOutcome:
    """One arm's complete rebalance, including any Dreamer round it needed."""

    state: MemoryState
    decisions: tuple[PolicyDecision, ...]
    created_summary: Memory | None
    summary_source_memory_ids: tuple[MemoryId, ...]
    metadata: tuple[CallMetadata, ...] = ()
    """One record per Dreamer call this rebalance made. Empty for the five arms
    that never compress, and for a summarising arm that fitted the budget."""


@dataclass(frozen=True, slots=True)
class ArmResult:
    """One staged arm-cycle: the state it would advance to, and its record."""

    arm_id: ArmId
    state: MemoryState
    snapshot: ArmCycleSnapshot


@dataclass(frozen=True, slots=True)
class StagedCycle:
    """Six arm results, complete and validated, not yet committed."""

    cycle: int
    stimulus: StimulusRecord
    results: tuple[ArmResult, ...]

    @property
    def snapshots(self) -> tuple[ArmCycleSnapshot, ...]:
        """The six snapshots, in configured arm order."""
        return tuple(result.snapshot for result in self.results)


@dataclass(frozen=True, slots=True)
class CheckpointRecord:
    """One arm's answers to the fixed question set at one checkpoint.

    Never admitted to memory and never able to move a citation statistic. Carried
    beside the run rather than inside an arm's state, so that it is structurally
    impossible for a measurement to become part of what is measured.
    """

    run_id: str
    arm_id: ArmId
    cycle: int
    interview_version: str
    result: InterviewResult
    active_memory_ids: tuple[MemoryId, ...]
    completed_at: datetime


# --------------------------------------------------------------------- engine


@dataclass
class PilotEngine:
    """Runs one pilot experiment locally, in memory, over the existing packages.

    Owns no persistence and no I/O. It owns the *sequence*: which cycle is next, what
    every arm is shown, in what order the mechanism and the model take their turns,
    and the rule that six arms advance together or not at all.
    """

    configuration: PilotRunConfiguration
    bundle: ProtocolBundle
    gateway: ModelGateway
    clock: Callable[[], datetime] = _utc_now
    budget: ModelCallBudget = field(init=False)
    status: RunStatus = field(default=RunStatus.INITIALIZED, init=False)
    current_cycle: int = field(default=0, init=False)
    _states: dict[ArmId, MemoryState] = field(default_factory=dict, init=False, repr=False)
    _policies: Mapping[ArmId, MemoryPolicy] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        """Build the per-run call budget and the mechanisms this protocol configures."""
        self.configuration.require_canonical_ready()
        self.budget = ModelCallBudget(limits=self.configuration.model_call_limits)
        self._policies = policies_for(self.configuration.policy_configuration)

    # ------------------------------------------------------------ initialisation

    def initialize_pilot_run(self) -> RunSnapshot:
        """Install the seed world into every arm, identically.

        Every arm receives the same twelve texts, with the same token counts, in the
        same order. The only thing that differs is the arm-scoped identifier each one
        takes, which is a consequence of memories belonging to exactly one arm and not
        a difference the mechanism can see.

        Raises:
            UnsatisfiableBudgetError: The seed set does not fit the budget every arm
                starts from, which is a calibration failure rather than a run one.
        """
        seeds = self.bundle.seed_world.memories
        total = sum(seed.token_count or 0 for seed in seeds)
        if total > self.configuration.memory_budget_tokens:
            msg = (
                f"the seed set costs {total} tokens, over the "
                f"{self.configuration.memory_budget_tokens}-token budget every arm starts from"
            )
            raise UnsatisfiableBudgetError(msg, run_id=self.configuration.run_id)

        self._states = {
            arm_id: self._install_seeds(arm_id, seeds) for arm_id in self.configuration.arms
        }
        self.current_cycle = 0
        self.status = RunStatus.INITIALIZED
        return self.run_snapshot()

    def _install_seeds(self, arm_id: ArmId, seeds: Sequence[SeedMemorySpec]) -> MemoryState:
        """Build one arm's starting state from the seed world."""
        state = MemoryState(run_id=self.configuration.run_id, arm_id=arm_id)
        for seed in seeds:
            state = state.admit(
                [
                    state.mint(
                        text=seed.text,
                        token_count=seed.token_count or 1,
                        memory_kind=MemoryKind.SEED,
                        cycle=0,
                    )
                ]
            )
        return state

    # ----------------------------------------------------------------- queries

    def state_of(self, arm_id: ArmId) -> MemoryState:
        """Return one arm's current memory.

        Raises:
            KeyError: The run does not configure that arm.
        """
        return self._states[arm_id]

    def run_snapshot(self) -> RunSnapshot:
        """The whole run's state, sealed with its own digest."""
        return RunSnapshot(
            configuration=self.configuration,
            status=self.status,
            current_cycle=self.current_cycle,
            arm_states={arm.value: state for arm, state in self._states.items()},
            usage=self.budget.usage,
            updated_at=self.clock(),
        ).sealed()

    # --------------------------------------------------------------- one cycle

    def prepare_cycle(self, cycle: int) -> StimulusRecord:
        """Check that ``cycle`` is next, and load the one stimulus every arm receives.

        Raises:
            CycleSequenceError: The run has not been initialised, the cycle is not the
                current one plus one, or it is past the configured length.
        """
        if not self._states:
            msg = "the run has no arm states; call initialize_pilot_run() first"
            raise CycleSequenceError(msg)
        if cycle != self.current_cycle + 1:
            msg = (
                f"cycle {cycle} was requested but this run is at cycle "
                f"{self.current_cycle}; the next cycle is {self.current_cycle + 1}"
            )
            raise CycleSequenceError(msg)
        if cycle > self.configuration.max_cycles:
            msg = f"cycle {cycle} is past the configured {self.configuration.max_cycles}"
            raise CycleSequenceError(msg)
        return _stimulus_record(self.bundle.stimulus_deck.for_cycle(cycle))

    def generate_arm_cycle(
        self, arm_id: ArmId, *, cycle: int, stimulus: StimulusRecord
    ) -> ArmGeneration:
        """Write one arm's thought and validate what it claimed to have used.

        The writer is shown the cycle number, this cycle's text, and the active
        memories. Not the arm, not the mechanism, not another arm's output, not a
        later stimulus, and not the truth ledger. The gateway enforces that; this
        method simply has nothing else to give it.

        Raises:
            ModelCallBudgetExceeded: This cycle has no writer call left.
            ModelInvocationError: The call failed after every permitted attempt.
        """
        state = self._states[arm_id]
        self.budget.spend(ModelRole.WRITER)
        result = self.gateway.writer.write(
            cycle=cycle,
            stimulus_text=stimulus.text,
            active_memories=state.active_memories,
        )
        self.budget.record(result.metadata)

        accepted, rejected = validate_claims(state, result.cited_memory_ids)
        # Zipped rather than looked up: a writer that cites one memory twice made two
        # claims with two different spans, and collapsing them into a map would lose
        # the second before the rejection record could name it.
        claims = tuple(
            CitationClaim(
                run_id=self.configuration.run_id,
                arm_id=arm_id,
                cycle=cycle,
                memory_id=memory_id,
                claim_index=index,
                quoted_span=claim.journal_span,
            )
            for index, (memory_id, claim) in enumerate(
                zip(result.cited_memory_ids, result.output.claimed_citations, strict=True)
            )
        )
        spans = {claim.memory_id: claim.quoted_span or "" for claim in claims}
        return ArmGeneration(
            arm_id=arm_id,
            writer=result,
            claims=claims,
            citations=self._citations(arm_id, cycle=cycle, accepted=accepted, spans=spans),
            rejected=rejected,
        )

    def _citations(
        self,
        arm_id: ArmId,
        *,
        cycle: int,
        accepted: Sequence[MemoryId],
        spans: Mapping[MemoryId, str],
    ) -> tuple[VerifiedCitation, ...]:
        """Turn validated claims into the citations a mechanism reads.

        In ``CLAIMED_VALIDATED`` mode the auditor is never called, and the record says
        so: ``auditor_version`` names the structural validator, not a model. Anything
        reading these later can tell an unaudited citation from an audited one without
        having to know which protocol produced it.
        """
        if self.configuration.citation_mode is not CitationMode.CLAIMED_VALIDATED:
            msg = (
                f"citation mode {self.configuration.citation_mode.value} is not "
                f"implemented by the pilot engine; see docs/pilot-scope.md"
            )
            raise NotImplementedError(msg)
        return tuple(
            VerifiedCitation(
                run_id=self.configuration.run_id,
                arm_id=arm_id,
                cycle=cycle,
                memory_id=memory_id,
                source=CitationSource.WRITER,
                auditor_version=CLAIMED_VALIDATOR_VERSION,
                evidence=spans[memory_id],
            )
            for memory_id in accepted
        )

    def rebalance_arm_memory(
        self, arm_id: ArmId, state: MemoryState, context: CycleContext
    ) -> RebalanceOutcome:
        """Apply the arm's mechanism, writing a Dreamer summary whenever it asks for one.

        The mechanism decides *what* is compressed and how large the result may be; a
        model writes the words; the mechanism then charges the result against the same
        budget as any other memory and decides whether the arm is done. The loop exists
        because one compression is not always enough, and each round is committed to
        the state before the next is planned.

        Raises:
            ModelCallBudgetExceeded: The arm asked for more summaries than this cycle
                allows, which stops the run rather than spending.
            UnsatisfiableBudgetError: No legal decision reaches the budget.
        """
        policy = self._policies[arm_id]
        budget = self.configuration.budget
        decision = policy.rebalance(state, budget, context)
        decisions = [decision]
        metadata: list[CallMetadata] = []
        created: Memory | None = None
        sources: tuple[MemoryId, ...] = ()

        while (plan := decision.compression_plan) is not None:
            if not isinstance(policy, CompressingMemoryPolicy):
                msg = f"{arm_id.value} asked for a compression but cannot commit one"
                raise TypeError(msg)
            # Commit whatever this decision already did before planning the next
            # round. The plan reserved the identifier the *next* free slot will take,
            # so the state has to have moved on for that reservation to be honoured.
            state = state.apply(decision)
            created, call = self._write_summary(state, plan=plan, cycle=context.cycle)
            metadata.append(call)
            sources = plan.source_memory_ids
            decision = policy.finalize_compression(state, budget, context, plan, created)
            decisions.append(decision)

        return RebalanceOutcome(
            state=state.apply(decision),
            decisions=tuple(decisions),
            created_summary=created,
            summary_source_memory_ids=sources,
            metadata=tuple(metadata),
        )

    def _write_summary(
        self, state: MemoryState, *, plan: CompressionPlan, cycle: int
    ) -> tuple[Memory, CallMetadata]:
        """Call the Dreamer for one plan and mint the memory its text becomes.

        Raises:
            ModelCallBudgetExceeded: This cycle has no summary call left.
            ModelInvocationError: The call failed after every permitted attempt.
        """
        active = {memory.memory_id: memory for memory in state.active_memories}
        sources = [active[memory_id] for memory_id in plan.source_memory_ids]
        self.budget.spend(ModelRole.SUMMARIZER)
        result = self.gateway.summarizer.summarize(plan=plan, sources=sources)
        self.budget.record(result.metadata)
        summary = state.mint(
            text=result.output.summary_text,
            token_count=result.summary_tokens,
            memory_kind=MemoryKind.SUMMARY,
            cycle=cycle,
            parent_memory_ids=plan.source_memory_ids,
        )
        return summary, result.metadata

    def finalize_arm_result(
        self,
        arm_id: ArmId,
        *,
        cycle: int,
        stimulus: StimulusRecord,
        generation: ArmGeneration,
    ) -> ArmResult:
        """Fold one arm's citations and candidate in, rebalance, and seal the record.

        Raises:
            ModelCallBudgetExceeded: The arm needed a call this cycle cannot afford.
            UnsatisfiableBudgetError: No legal decision reaches the budget.
        """
        before = self._states[arm_id]
        scored = before.record_cycle_citations(
            generation.citations,
            cycle=cycle,
            decay=self.configuration.heavy_hitter_citation_decay,
        )
        candidate_text = generation.writer.output.candidate_memory
        candidate = scored.mint(
            text=candidate_text,
            token_count=self.gateway.token_counter.count(candidate_text),
            memory_kind=MemoryKind.GENERATED,
            cycle=cycle,
            source_stimulus_id=stimulus.stimulus_id,
        )
        admitted = scored.admit([candidate])

        context = CycleContext(
            run_id=self.configuration.run_id,
            arm_id=arm_id,
            cycle=cycle,
            stimulus_id=stimulus.stimulus_id,
            protocol_version=self.configuration.protocol_version,
            prompt_version=self.configuration.writer_prompt_version,
            run_random_seed=self.configuration.random_seed,
        )
        outcome = self.rebalance_arm_memory(arm_id, admitted, context)
        final = outcome.state

        retirements = [r for decision in outcome.decisions for r in decision.retirements]
        # Every retired memory is still in the arm's state; nothing is ever deleted.
        # That is what lets the snapshot carry the text of what was lost.
        known = {memory.memory_id: memory for memory in final.memories}
        retired = tuple(
            RetiredMemoryRecord(
                memory_id=retirement.memory_id,
                status=retirement.status,
                reason=retirement.reason,
                token_count=known[retirement.memory_id].token_count,
                text=known[retirement.memory_id].text,
            )
            for retirement in retirements
        )
        snapshot = ArmCycleSnapshot(
            run_id=self.configuration.run_id,
            arm_id=arm_id,
            cycle=cycle,
            stimulus=stimulus,
            active_memory_ids_before=before.active_memory_ids,
            memory_statistics_before_rebalance=tuple(
                MemoryStatistic.of(memory) for memory in admitted.active_memories
            ),
            tokens_before=admitted.active_tokens,
            journal_entry=generation.writer.output.journal_entry,
            candidate_memory=candidate_text,
            candidate_memory_id=candidate.memory_id,
            claimed_citations=generation.claims,
            validated_citations=generation.citations,
            rejected_claims=generation.rejected,
            policy_decisions=outcome.decisions,
            created_summary=outcome.created_summary,
            summary_source_memory_ids=outcome.summary_source_memory_ids,
            retired_memories=retired,
            compressed_memory_ids=tuple(
                r.memory_id for r in retirements if r.status is MemoryStatus.COMPRESSED
            ),
            active_memory_ids_after=final.active_memory_ids,
            tokens_after=final.active_tokens,
            budget_tokens=self.configuration.memory_budget_tokens,
            state_hash=final.state_hash,
            model_metadata=(generation.writer.metadata, *outcome.metadata),
            policy_version=outcome.decisions[-1].policy_version,
            prompt_hashes=self._prompt_hashes(),
            simulated=self.gateway.simulated,
            completed_at=self.clock(),
        ).sealed()
        return ArmResult(arm_id=arm_id, state=final, snapshot=snapshot)

    def _prompt_hashes(self) -> dict[str, str]:
        """The digest of every prompt this run's cycles can use."""
        library = self.gateway.prompts
        version = self.configuration.writer_prompt_version
        return {template.identifier: template.digest for template in library.manifest(version)} | {
            "prompt_set": library.prompt_set_digest(version)
        }

    # ------------------------------------------------------------ staging

    def stage_cycle(self, cycle: int) -> StagedCycle:
        """Run all six arms for ``cycle`` without advancing the run.

        Arms are generated with bounded concurrency and then finalised in configured
        order. The concurrency is over the *model calls*, which are independent; the
        ordering of what is stored is not left to whichever call returned first.

        Raises:
            CycleSequenceError: ``cycle`` is not the next one.
            ModelCallBudgetExceeded: The cycle asked for a call it cannot afford.
            Exception: Whatever one arm raised. Nothing has been committed.
        """
        stimulus = self.prepare_cycle(cycle)
        self.budget.open_cycle(cycle, checkpoint=False)
        arms = self.configuration.arms

        with ThreadPoolExecutor(
            max_workers=self.configuration.max_parallel_model_calls,
            thread_name_prefix=f"pilot-c{cycle}",
        ) as pool:
            futures = {
                arm_id: pool.submit(self.generate_arm_cycle, arm_id, cycle=cycle, stimulus=stimulus)
                for arm_id in arms
            }
            generations = {arm_id: future.result() for arm_id, future in futures.items()}

        # Finalisation is sequential and in configured order. It calls the Dreamer,
        # and a summary call has to be charged against a per-cycle allowance that two
        # arms racing for the last slot would make nondeterministic.
        results = tuple(
            self.finalize_arm_result(
                arm_id, cycle=cycle, stimulus=stimulus, generation=generations[arm_id]
            )
            for arm_id in arms
        )
        staged = StagedCycle(cycle=cycle, stimulus=stimulus, results=results)
        self.validate_staged_cycle(staged)
        return staged

    def validate_staged_cycle(self, staged: StagedCycle) -> None:
        """Assert what must be true of six arms taken together before any advances.

        Per-arm invariants are already enforced by the domain and by the snapshot
        model. What only a cross-arm check can catch is an arm missing, an arm
        appearing twice, an arm that received a different stimulus, or an arm that
        drifted onto a different cycle.

        Raises:
            ValueError: The staged cycle is not six arms of one cycle on one stimulus.
        """
        expected = self.configuration.arms
        staged_arms = tuple(result.arm_id for result in staged.results)
        problems: list[str] = []
        if staged_arms != expected:
            problems.append(
                f"staged arms {[a.value for a in staged_arms]} are not the configured "
                f"{[a.value for a in expected]}, in order"
            )
        for result in staged.results:
            snapshot = result.snapshot
            if snapshot.cycle != staged.cycle:
                problems.append(f"{snapshot.arm_id.value} staged cycle {snapshot.cycle}")
            if snapshot.stimulus.stimulus_id != staged.stimulus.stimulus_id:
                problems.append(
                    f"{snapshot.arm_id.value} received {snapshot.stimulus.stimulus_id}, "
                    f"not {staged.stimulus.stimulus_id}"
                )
            if snapshot.tokens_after > self.configuration.memory_budget_tokens:
                problems.append(
                    f"{snapshot.arm_id.value} ends over budget at {snapshot.tokens_after}"
                )
            if not snapshot.verify_hash():
                problems.append(f"{snapshot.arm_id.value} snapshot hash does not match")
        if problems:
            msg = f"cycle {staged.cycle} cannot be committed:\n  - " + "\n  - ".join(problems)
            raise ValueError(msg)

    def complete_cycle_in_memory(self, staged: StagedCycle) -> tuple[ArmCycleSnapshot, ...]:
        """Advance the run to a staged cycle that has already been validated.

        The one place arm state changes. Every state is replaced in a single pass
        after the checks have passed, so there is no window in which some arms are on
        the new cycle and some are not.

        Raises:
            CycleSequenceError: The staged cycle is not the one that comes next.
        """
        if staged.cycle != self.current_cycle + 1:
            msg = (
                f"staged cycle {staged.cycle} is not the next cycle "
                f"({self.current_cycle + 1}); refusing to advance"
            )
            raise CycleSequenceError(msg)
        self.validate_staged_cycle(staged)
        self._states = {result.arm_id: result.state for result in staged.results}
        self.current_cycle = staged.cycle
        self.status = (
            RunStatus.COMPLETED
            if staged.cycle == self.configuration.max_cycles
            else RunStatus.RUNNING
        )
        return staged.snapshots

    def run_cycle(self, cycle: int) -> tuple[ArmCycleSnapshot, ...]:
        """Stage, validate, and commit one cycle.

        Raises:
            Exception: Anything staging raised. The run is left where it was.
        """
        try:
            staged = self.stage_cycle(cycle)
        except Exception:
            self.status = RunStatus.FAILED
            raise
        return self.complete_cycle_in_memory(staged)

    # ----------------------------------------------------------- checkpoints

    def run_checkpoint(self, cycle: int) -> tuple[CheckpointRecord, ...]:
        """Ask every arm the fixed question set, changing nothing.

        Spent from a checkpoint allowance rather than the cycle's, so that the normal
        cycle limit stays exactly what the protocol declares: six writer calls, at
        most two Dreamer summaries, and nothing else.

        Raises:
            CycleSequenceError: ``cycle`` is not a configured checkpoint, or the run
                has not reached it.
            ModelCallBudgetExceeded: The checkpoint allowance is exhausted.
        """
        if not self.configuration.is_checkpoint(cycle):
            msg = f"cycle {cycle} is not a checkpoint of this protocol"
            raise CycleSequenceError(msg)
        if cycle != self.current_cycle:
            msg = f"the run is at cycle {self.current_cycle}; checkpoint {cycle} is not now"
            raise CycleSequenceError(msg)

        self.budget.open_cycle(cycle, checkpoint=True)
        questions = [
            InterviewQuestion(question_id=q.question_id, text=q.text)
            for q in self.bundle.interview.questions
        ]
        records: list[CheckpointRecord] = []
        for arm_id in self.configuration.arms:
            state = self._states[arm_id]
            self.budget.spend(ModelRole.INTERVIEWER)
            result = self.gateway.interviewer.interview(
                questions=questions, active_memories=state.active_memories
            )
            self.budget.record(result.metadata)
            records.append(
                CheckpointRecord(
                    run_id=self.configuration.run_id,
                    arm_id=arm_id,
                    cycle=cycle,
                    interview_version=self.configuration.interview_version,
                    result=result,
                    active_memory_ids=state.active_memory_ids,
                    completed_at=self.clock(),
                )
            )
        return tuple(records)


def _stimulus_record(spec: StimulusSpec) -> StimulusRecord:
    """Project a protocol stimulus onto the fields a snapshot stores."""
    return StimulusRecord(
        stimulus_id=spec.stimulus_id,
        cycle=spec.cycle,
        phase=spec.phase,
        reliability=spec.reliability.value,
        text=spec.text,
    )
