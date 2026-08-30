"""The application service: one cycle, taken safely, exactly once.

The engine knows how to produce a cycle. This knows when it is allowed to, who is
allowed to, and what happens when the same cycle is asked for twice. It holds a
repository *protocol*, never an adapter, so the same service runs on SQLite locally
and on DynamoDB later without knowing which.

The shape of one advance:

    lock -> load -> stage (or reuse) -> prepare -> commit -> release

Every step is idempotent on its own. Taken together they mean a scheduler that fires
twice, a process that dies between staging and committing, and a developer who runs
the command again all converge on the same single committed cycle -- and none of them
calls six writers twice.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from attention_sink.domain import ArmId, MemoryState
from attention_sink.model_gateway import InterviewQuestion, ModelGateway
from attention_sink.pilot.budget import ModelCallBudget, ModelUsage, cycle_calls
from attention_sink.pilot.canonical import canonical_digest
from attention_sink.pilot.configuration import PilotRunConfiguration, RunKind
from attention_sink.pilot.engine import CheckpointRecord, PilotEngine, StagedCycle
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.repositories import (
    PersistenceError,
    PilotRepository,
    PreparedCycle,
    RunRecord,
    StoredInterview,
)
from attention_sink.pilot.snapshots import ArmCycleSnapshot, RunStatus

__all__ = [
    "CycleOutcome",
    "PilotService",
    "RunNotFound",
    "RunPaused",
    "ServiceError",
]


class ServiceError(RuntimeError):
    """The service refused an operation. Nothing was written."""


class RunNotFound(ServiceError):
    """No such run in the store."""


class RunPaused(ServiceError):
    """An operator paused this run. The scheduler will not advance it."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True, slots=True)
class CycleOutcome:
    """What one advance did, and whether it did anything at all."""

    run: RunRecord
    cycle: int
    snapshots: tuple[ArmCycleSnapshot, ...]
    checkpoints: tuple[CheckpointRecord, ...] = ()
    reused_prepared_cycle: bool = False
    """True when a retry reused a staged cycle instead of calling six writers again."""

    already_committed: bool = False
    """True when the cycle was already committed and this call changed nothing."""


@dataclass
class PilotService:
    """Everything a caller needs to advance a persisted run by one cycle."""

    repository: PilotRepository
    bundle: ProtocolBundle
    gateway: ModelGateway
    clock: Callable[[], datetime] = _utc_now
    lock_ttl_seconds: int = 300
    engine_clock: Callable[[], datetime] | None = None
    """Injected into every engine this service builds, so a snapshot hash can be
    pinned in a test without the service having to know that is why."""

    _last_usage: ModelUsage = field(default_factory=ModelUsage, repr=False)

    # ------------------------------------------------------------------ runs

    def create_run(
        self,
        *,
        run_id: str,
        configuration: PilotRunConfiguration,
    ) -> RunRecord:
        """Create a run and install the seed world into all six arms.

        The seeds are committed as the run's cycle-0 state before any cycle exists,
        so a run that was created but never advanced still has six identical arms to
        interview at checkpoint 0.

        Raises:
            PersistenceError: A run with that identifier already exists.
        """
        now = self.clock()
        record = RunRecord(
            run_id=run_id,
            run_kind=configuration.run_kind,
            status=RunStatus.INITIALIZED,
            current_cycle=0,
            version=0,
            configuration=configuration,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_run(record)
        engine = self._fresh_engine(configuration)
        engine.initialize_pilot_run()
        for arm_id in configuration.arms:
            self._store_initial_state(run_id, arm_id, engine.state_of(arm_id))
        return record

    def _store_initial_state(self, run_id: str, arm_id: ArmId, state: MemoryState) -> None:
        """Write one arm's cycle-0 state through the repository's own commit path.

        Uses ``store_prepared_cycle``/``commit_cycle`` nowhere: cycle 0 is not a
        cycle, it is the starting condition, and routing it through the commit
        machinery would make "cycle 0 committed" a state the invariants have to
        special-case forever.
        """
        self.repository.seed_arm_state(run_id, arm_id=arm_id, state=state)

    def get_run(self, run_id: str) -> RunRecord:
        """Load one run's head.

        Raises:
            RunNotFound: No such run.
        """
        record = self.repository.get_run(run_id)
        if record is None:
            msg = f"no run {run_id}"
            raise RunNotFound(msg)
        return record

    # -------------------------------------------------------------- one cycle

    def run_next_cycle(self, run_id: str, *, invocation_id: str | None = None) -> CycleOutcome:
        """Advance ``run_id`` by exactly one cycle, or explain why it did not.

        Raises:
            RunNotFound: No such run.
            RunPaused: The run is paused.
            ServiceError: The run is already complete.
            LockNotHeld: Another invocation holds the lock.
        """
        run = self.get_run(run_id)
        if run.paused:
            msg = f"run {run_id} is paused; resume it before advancing"
            raise RunPaused(msg)
        if run.is_complete:
            msg = f"run {run_id} has completed all {run.configuration.maximum_cycles} cycles"
            raise ServiceError(msg)

        cycle = run.next_cycle
        invocation = invocation_id or uuid.uuid4().hex
        lock = self.repository.acquire_cycle_lock(
            run_id, cycle=cycle, invocation_id=invocation, ttl_seconds=self.lock_ttl_seconds
        )
        try:
            prepared, reused = self._prepare(run, cycle=cycle, invocation_id=invocation)
            committed = self.repository.commit_cycle(
                run_id,
                cycle=cycle,
                token=lock.token,
                content_hash=prepared.content_hash,
                version=run.version,
            )
        finally:
            self.repository.release_cycle_lock(run_id, token=lock.token)

        checkpoints: tuple[CheckpointRecord, ...] = ()
        if committed.configuration.is_checkpoint(cycle):
            checkpoints = self.run_checkpoint(run_id, cycle=cycle)
        return CycleOutcome(
            run=self.get_run(run_id),
            cycle=cycle,
            snapshots=prepared.snapshots,
            checkpoints=checkpoints,
            reused_prepared_cycle=reused,
        )

    def _prepare(
        self, run: RunRecord, *, cycle: int, invocation_id: str
    ) -> tuple[PreparedCycle, bool]:
        """Return the staged cycle for ``cycle``, generating it only if needed.

        Reuse is the whole point. A retry after a failed commit must not call six
        writers again: the generations are already recorded, and re-generating them
        would produce a *different* cycle for the same cycle number, which is a
        conflict rather than a retry.
        """
        existing = self.repository.get_prepared_cycle(run.run_id, cycle=cycle)
        if existing is not None:
            return existing, True

        staged = self._stage(run, cycle=cycle)
        prepared = PreparedCycle(
            run_id=run.run_id,
            cycle=cycle,
            invocation_id=invocation_id,
            snapshots=tuple(result.snapshot for result in staged.results),
            arm_states={r.arm_id.value: r.state for r in staged.results},
            usage=self._last_usage,
            created_at=self.clock(),
        ).sealed()
        return self.repository.store_prepared_cycle(prepared), False

    def _stage(self, run: RunRecord, *, cycle: int) -> StagedCycle:
        """Run one cycle in memory from the persisted state, without committing it."""
        engine = self._engine_at(run)
        staged = engine.stage_cycle(cycle)
        self._last_usage = _merged_usage(run, engine.budget)
        return staged

    # ------------------------------------------------------------ checkpoints

    def run_checkpoint(self, run_id: str, *, cycle: int) -> tuple[CheckpointRecord, ...]:
        """Interview every arm at ``cycle`` and persist the answers.

        Idempotent: an interview already stored for an arm and cycle is returned
        rather than re-asked, so a scheduler that fires a checkpoint twice does not
        spend six interviewer calls twice or store two measurements of one moment.

        Raises:
            RunNotFound: No such run.
            ServiceError: ``cycle`` is not a configured checkpoint, or the run has
                not reached it.
        """
        run = self.get_run(run_id)
        if not run.configuration.is_checkpoint(cycle):
            msg = f"cycle {cycle} is not a checkpoint of {run.configuration.protocol_version}"
            raise ServiceError(msg)
        if cycle > run.current_cycle:
            msg = f"run {run_id} is at cycle {run.current_cycle}; checkpoint {cycle} is not yet"
            raise ServiceError(msg)

        stored = {i.arm_id for i in self.repository.get_interviews(run_id, cycle=cycle)}
        outstanding = [arm for arm in run.configuration.arms if arm not in stored]
        if not outstanding:
            return ()

        engine = self._engine_at(run)
        records = engine.run_checkpoint(cycle, arms=outstanding)
        for record in records:
            self.repository.store_interview(_stored_interview(run, record, engine=engine))
        # A checkpoint follows the commit that snapshotted usage, so its interviewer
        # calls have to be folded in separately or they are never counted at all.
        self.repository.add_usage(run_id, usage=engine.budget.usage)
        return records

    # ---------------------------------------------------------------- engines

    def _fresh_engine(self, configuration: PilotRunConfiguration) -> PilotEngine:
        engine = PilotEngine(configuration=configuration, bundle=self.bundle, gateway=self.gateway)
        if self.engine_clock is not None:
            engine.clock = self.engine_clock
        return engine

    def _engine_at(self, run: RunRecord) -> PilotEngine:
        """An engine restored to exactly the state the store holds.

        Rehydrated rather than kept: the service is stateless between calls, which is
        what makes it the same object a Lambda handler will construct per invocation.
        """
        engine = self._fresh_engine(run.configuration)
        # What the run has already spent, so the per-run ceiling bounds the run and
        # not this one invocation. A deployment advances one cycle per process, so a
        # budget that started empty every time would never reach any ceiling at all.
        engine.budget.previously_spent = cycle_calls(run.usage)
        engine.initialize_pilot_run()
        states = self.repository.get_all_current_arm_states(run.run_id)
        missing = [arm.value for arm in run.configuration.arms if arm.value not in states]
        if missing:
            msg = f"run {run.run_id} has no stored state for {', '.join(missing)}"
            raise PersistenceError(msg)
        engine.restore(
            {ArmId(name): state for name, state in states.items()},
            cycle=run.current_cycle,
            status=run.status,
        )
        return engine


def _merged_usage(run: RunRecord, budget: ModelCallBudget) -> ModelUsage:
    """This cycle's spend folded into everything the run had already spent."""
    cycle_usage = budget.usage
    previous = run.usage
    roles = {**previous.calls_by_role}
    for role, count in cycle_usage.calls_by_role.items():
        roles[role] = roles.get(role, 0) + count
    return ModelUsage(
        calls_by_role=roles,
        ledger=(*previous.ledger, *cycle_usage.ledger),
        total_calls=previous.total_calls + cycle_usage.total_calls,
        failed_calls=previous.failed_calls + cycle_usage.failed_calls,
        simulated_calls=previous.simulated_calls + cycle_usage.simulated_calls,
        input_tokens=previous.input_tokens + cycle_usage.input_tokens,
        output_tokens=previous.output_tokens + cycle_usage.output_tokens,
        retries=previous.retries + cycle_usage.retries,
    )


def _stored_interview(
    run: RunRecord, record: CheckpointRecord, *, engine: PilotEngine
) -> StoredInterview:
    """Project one checkpoint record onto the record the store keeps."""
    output = record.result.output
    state = engine.state_of(record.arm_id)
    answers: tuple[dict[str, object], ...] = tuple(
        {
            "question_id": answer.question_id,
            "answer": answer.answer,
            "cited_memory_refs": list(answer.cited_memory_refs),
            "stated_uncertainty": answer.stated_uncertainty,
        }
        for answer in output.answers
    )
    return StoredInterview(
        run_id=run.run_id,
        arm_id=record.arm_id,
        cycle=record.cycle,
        interview_version=record.interview_version,
        question_set_version=run.configuration.interview_version,
        answers=answers,
        reported_memory_ids=record.result.cited_memory_ids,
        stated_uncertainty=tuple(a.stated_uncertainty for a in output.answers),
        model_metadata=record.result.metadata.model_dump(mode="json"),
        # A fixture call records no prompt hash; the record still needs one that
        # says so rather than a blank the schema would reject.
        prompt_hash=record.result.metadata.prompt_hash or "unrecorded",
        input_state_hash=state.state_hash,
        completed_at=record.completed_at,
    ).sealed()


def interview_questions(bundle: ProtocolBundle) -> tuple[InterviewQuestion, ...]:
    """The fixed question set, as the gateway wants it."""
    return tuple(
        InterviewQuestion(question_id=question.question_id, text=question.text)
        for question in bundle.interview.questions
    )


def configuration_digest(configuration: PilotRunConfiguration) -> str:
    """A digest of everything that makes one run a different experiment from another."""
    return canonical_digest(configuration.model_dump(mode="json"))


def default_run_kind() -> RunKind:
    """What a run is unless a caller says otherwise. Never canonical in this phase."""
    return RunKind.LOCAL_FIXTURE
