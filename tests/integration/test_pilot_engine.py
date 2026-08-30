"""The cycle engine: six arms advancing together, or not at all.

Integration rather than unit because the subject is the seam. Every one of these
drives the real domain kernel, the real mechanisms, and the real gateway adapters over
a fixture invoker, because the thing being asserted is that they fit.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Sequence
from typing import Any

import pytest

from attention_sink.domain import (
    ArmId,
    Memory,
    MemoryKind,
    MemoryStatus,
    UnsatisfiableBudgetError,
)
from attention_sink.model_gateway import ModelGateway, ModelRole, ThoughtWriter, WriterResult
from attention_sink.pilot import (
    CLAIMED_VALIDATOR_VERSION,
    CitationMode,
    CycleSequenceError,
    ModelCallBudgetExceeded,
    PilotEngine,
    ProtocolBundle,
    RunStatus,
    build_run,
    validate_claims,
)
from tests.conftest import fixed_clock

TIGHT_BUDGET = 160
"""Three tokens above the seed set, so the summarising arm compresses on cycle 1."""


def make_engine(bundle: ProtocolBundle, gateway: ModelGateway, **overrides: object) -> PilotEngine:
    """An initialised engine whose configuration may be tightened for one test."""
    base = build_run(bundle, run_id="run_engine", gateway=gateway)
    config = base.configuration.model_copy(update=overrides) if overrides else base.configuration
    engine = PilotEngine(configuration=config, bundle=bundle, gateway=gateway)
    engine.clock = fixed_clock
    engine.initialize_pilot_run()
    return engine


@dataclasses.dataclass
class FailOnNthWriter:
    """A writer that works until the nth call of a cycle and then does not."""

    inner: ThoughtWriter
    fail_on: int
    calls: int = 0

    def write(self, **kwargs: Any) -> WriterResult:
        """Delegate, except on the call this double was built to fail."""
        self.calls += 1
        if self.calls == self.fail_on:
            msg = f"writer call {self.calls} failed"
            raise RuntimeError(msg)
        return self.inner.write(**kwargs)


# ------------------------------------------------------------ initialisation


def test_all_six_arms_start_from_identical_memory(pilot_engine: PilotEngine):
    states = [pilot_engine.state_of(arm) for arm in pilot_engine.configuration.arms]
    texts = {tuple(m.text for m in state.active_memories) for state in states}
    tokens = {state.active_tokens for state in states}
    kinds = {tuple(m.memory_kind for m in state.active_memories) for state in states}
    assert len(texts) == 1, "arms began with different memories"
    assert len(tokens) == 1
    assert kinds == {(MemoryKind.SEED,) * 12}


def test_the_seed_order_is_the_protocol_order(
    pilot_engine: PilotEngine, pilot_bundle: ProtocolBundle
):
    expected = [seed.text for seed in pilot_bundle.seed_world.memories]
    for arm in pilot_engine.configuration.arms:
        assert [m.text for m in pilot_engine.state_of(arm).active_memories] == expected


def test_only_the_pinned_origin_arm_treats_a_seed_as_unforgettable(pilot_engine: PilotEngine):
    """Every arm holds the same seed; one arm's *mechanism* protects it."""
    for arm in pilot_engine.configuration.arms:
        assert not any(m.pinned for m in pilot_engine.state_of(arm).memories)
    pinned = pilot_engine.configuration.pinned_origin_memory_id
    assert pinned.startswith(f"mem_{ArmId.ARM_SINK.value}")


def test_the_run_starts_initialized_at_cycle_zero(pilot_engine: PilotEngine):
    assert pilot_engine.status is RunStatus.INITIALIZED
    assert pilot_engine.current_cycle == 0


# ----------------------------------------------------------------- sequencing


def test_a_cycle_out_of_order_is_refused(pilot_engine: PilotEngine):
    for cycle in (0, 2, 5, 24):
        with pytest.raises(CycleSequenceError, match="the next cycle is 1"):
            pilot_engine.prepare_cycle(cycle)


def test_a_cycle_cannot_be_run_twice(pilot_engine: PilotEngine):
    pilot_engine.run_cycle(1)
    assert pilot_engine.current_cycle == 1
    with pytest.raises(CycleSequenceError, match="the next cycle is 2"):
        pilot_engine.run_cycle(1)


def test_a_cycle_past_the_protocol_is_refused(pilot_engine: PilotEngine):
    engine = pilot_engine
    engine.current_cycle = engine.configuration.maximum_cycles
    with pytest.raises(CycleSequenceError, match="past the configured 24"):
        engine.prepare_cycle(25)


def test_an_uninitialised_run_has_nothing_to_prepare(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = PilotEngine(
        configuration=build_run(
            pilot_bundle, run_id="run_bare", gateway=pilot_gateway
        ).configuration,
        bundle=pilot_bundle,
        gateway=pilot_gateway,
    )
    with pytest.raises(CycleSequenceError, match="initialize_pilot_run"):
        engine.prepare_cycle(1)


# ------------------------------------------------------------------- staging


def test_every_arm_receives_the_same_stimulus(pilot_engine: PilotEngine):
    for cycle in (1, 2, 3):
        snapshots = pilot_engine.run_cycle(cycle)
        assert len({s.stimulus.stimulus_id for s in snapshots}) == 1
        assert len({s.stimulus.text for s in snapshots}) == 1
        assert snapshots[0].stimulus.cycle == cycle


def test_a_cycle_produces_exactly_six_snapshots_in_configured_order(pilot_engine: PilotEngine):
    snapshots = pilot_engine.run_cycle(1)
    assert len(snapshots) == 6
    assert tuple(s.arm_id for s in snapshots) == pilot_engine.configuration.arms


def test_the_stored_order_does_not_depend_on_completion_order(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """One worker and six workers must produce byte-identical snapshots."""
    serial = make_engine(pilot_bundle, pilot_gateway, max_parallel_model_calls=1)
    parallel = make_engine(pilot_bundle, pilot_gateway, max_parallel_model_calls=6)
    for cycle in (1, 2):
        one = serial.run_cycle(cycle)
        many = parallel.run_cycle(cycle)
        assert [s.arm_id for s in one] == [s.arm_id for s in many]
        assert [s.snapshot_hash for s in one] == [s.snapshot_hash for s in many]


def test_a_staged_cycle_is_not_committed_until_it_is_completed(pilot_engine: PilotEngine):
    before = {arm: pilot_engine.state_of(arm).state_hash for arm in pilot_engine.configuration.arms}
    staged = pilot_engine.stage_cycle(1)
    assert pilot_engine.current_cycle == 0
    assert {
        arm: pilot_engine.state_of(arm).state_hash for arm in pilot_engine.configuration.arms
    } == before

    pilot_engine.complete_cycle_in_memory(staged)
    assert pilot_engine.current_cycle == 1
    assert any(
        pilot_engine.state_of(arm).state_hash != before[arm]
        for arm in pilot_engine.configuration.arms
    )


def test_a_staged_cycle_missing_an_arm_is_refused(pilot_engine: PilotEngine):
    staged = pilot_engine.stage_cycle(1)
    short = dataclasses.replace(staged, results=staged.results[:5])
    with pytest.raises(ValueError, match="are not the configured"):
        pilot_engine.validate_staged_cycle(short)
    with pytest.raises(ValueError, match="are not the configured"):
        pilot_engine.complete_cycle_in_memory(short)


def test_a_staged_cycle_cannot_be_committed_out_of_order(pilot_engine: PilotEngine):
    staged = pilot_engine.stage_cycle(1)
    pilot_engine.complete_cycle_in_memory(staged)
    with pytest.raises(CycleSequenceError, match="not the next cycle"):
        pilot_engine.complete_cycle_in_memory(staged)


def test_one_failed_arm_leaves_all_six_states_unchanged(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway)
    engine.run_cycle(1)
    before = {arm: engine.state_of(arm).state_hash for arm in engine.configuration.arms}

    engine.gateway = dataclasses.replace(
        pilot_gateway, writer=FailOnNthWriter(pilot_gateway.writer, fail_on=3)
    )
    with pytest.raises(RuntimeError, match="writer call 3 failed"):
        engine.run_cycle(2)

    assert engine.status is RunStatus.FAILED
    assert engine.current_cycle == 1
    assert {arm: engine.state_of(arm).state_hash for arm in engine.configuration.arms} == before


# -------------------------------------------------------------------- budget


def test_no_arm_ever_exceeds_the_budget(pilot_engine: PilotEngine):
    budget = pilot_engine.configuration.memory_budget_tokens
    for cycle in range(1, 9):
        for snapshot in pilot_engine.run_cycle(cycle):
            assert snapshot.tokens_after <= budget
            assert pilot_engine.state_of(snapshot.arm_id).active_tokens <= budget


def test_a_cycle_may_not_make_a_seventh_writer_call(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway)
    limits = engine.configuration.model_call_limits
    engine.budget.limits = limits.model_copy(update={"writer_calls_per_cycle": 5})
    with pytest.raises(ModelCallBudgetExceeded, match="5 writer call"):
        engine.stage_cycle(1)
    assert engine.current_cycle == 0


def test_a_cycle_may_not_make_a_dreamer_call_it_cannot_afford(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway, memory_budget_tokens=TIGHT_BUDGET)
    limits = engine.configuration.model_call_limits
    engine.budget.limits = limits.model_copy(update={"summary_calls_per_cycle": 0})
    with pytest.raises(ModelCallBudgetExceeded, match="0 summarizer call"):
        engine.stage_cycle(1)
    assert engine.current_cycle == 0


def test_a_normal_cycle_spends_six_writers_and_nothing_else(pilot_engine: PilotEngine):
    pilot_engine.run_cycle(1)
    usage = pilot_engine.budget.usage
    assert usage.calls_by_role == {"writer": 6}
    assert usage.simulated_calls == 6


def test_a_budget_no_arm_can_satisfy_stops_the_run(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway)
    engine.configuration = engine.configuration.model_copy(update={"memory_budget_tokens": 1})
    with pytest.raises(UnsatisfiableBudgetError):
        engine.stage_cycle(1)
    assert engine.current_cycle == 0


# ------------------------------------------------------------------ citations


def test_citations_are_limited_to_memories_the_arm_currently_holds(pilot_engine: PilotEngine):
    for cycle in (1, 2, 3):
        for snapshot in pilot_engine.run_cycle(cycle):
            held = set(snapshot.active_memory_ids_before)
            assert {c.memory_id for c in snapshot.validated_citations} <= held
            assert {c.memory_id for c in snapshot.claimed_citations} <= held


def test_a_validated_citation_names_the_validator_rather_than_an_auditor(
    pilot_engine: PilotEngine,
):
    for snapshot in pilot_engine.run_cycle(1):
        for citation in snapshot.validated_citations:
            assert citation.auditor_version == CLAIMED_VALIDATOR_VERSION
            assert citation.updates_memory_state


def test_the_pilot_never_calls_the_auditor(pilot_engine: PilotEngine):
    for cycle in range(1, 5):
        pilot_engine.run_cycle(cycle)
    assert "auditor" not in pilot_engine.budget.usage.calls_by_role


def test_a_duplicate_claim_counts_once_and_is_recorded_as_rejected(pilot_engine: PilotEngine):
    state = pilot_engine.state_of(ArmId.ARM_FIFO)
    first, second = state.active_memory_ids[0], state.active_memory_ids[1]
    accepted, rejected = validate_claims(state, [first, second, first])
    assert accepted == (first, second)
    assert [(r.memory_id, r.reason) for r in rejected] == [(first, "duplicate")]


def test_a_claim_on_a_retired_memory_is_rejected(pilot_engine: PilotEngine):
    state = pilot_engine.state_of(ArmId.ARM_FIFO)
    target = state.active_memories[0]
    retired = state.model_copy(
        update={
            "memories": (
                target.retire(status=MemoryStatus.EVICTED, cycle=1),
                *state.memories[1:],
            )
        }
    )
    accepted, rejected = validate_claims(retired, [target.memory_id])
    assert accepted == ()
    assert [(r.memory_id, r.reason) for r in rejected] == [(target.memory_id, "not_active")]


def test_a_claim_on_a_memory_this_arm_never_had_is_rejected(pilot_engine: PilotEngine):
    state = pilot_engine.state_of(ArmId.ARM_FIFO)
    accepted, rejected = validate_claims(state, ["mem_arm_lru_000000"])
    assert accepted == ()
    assert rejected[0].reason == "not_active"


# -------------------------------------------------------------------- dreamer


def summaries(memories: Sequence[Memory]) -> list[Memory]:
    return [m for m in memories if m.memory_kind is MemoryKind.SUMMARY]


def test_only_the_summarising_arm_ever_compresses(pilot_engine: PilotEngine):
    for cycle in range(1, 13):
        pilot_engine.run_cycle(cycle)
    for arm in pilot_engine.configuration.arms:
        created = summaries(pilot_engine.state_of(arm).memories)
        if arm is ArmId.ARM_SUMMARY:
            assert created, "the summarising arm never compressed under pressure"
        else:
            assert not created


def test_every_dreamer_summary_records_its_lineage(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway, memory_budget_tokens=TIGHT_BUDGET)
    snapshot = next(s for s in engine.run_cycle(1) if s.arm_id is ArmId.ARM_SUMMARY)

    summary = snapshot.created_summary
    assert summary is not None
    assert summary.memory_kind is MemoryKind.SUMMARY
    assert len(summary.parent_memory_ids) >= 2
    assert set(summary.parent_memory_ids) == set(snapshot.summary_source_memory_ids)
    assert set(summary.parent_memory_ids) <= set(snapshot.compressed_memory_ids)

    state = engine.state_of(ArmId.ARM_SUMMARY)
    edges = [e for e in state.lineage_edges if e.child_memory_id == summary.memory_id]
    assert {e.parent_memory_id for e in edges} == set(summary.parent_memory_ids)
    for parent in summary.parent_memory_ids:
        source = state.get(parent)
        assert source is not None
        assert source.status is MemoryStatus.COMPRESSED


def test_a_compressed_source_keeps_its_text_in_the_snapshot(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway, memory_budget_tokens=TIGHT_BUDGET)
    snapshot = next(s for s in engine.run_cycle(1) if s.arm_id is ArmId.ARM_SUMMARY)
    compressed = [r for r in snapshot.retired_memories if r.status is MemoryStatus.COMPRESSED]
    assert compressed
    assert all(record.text.strip() for record in compressed)


def test_a_dreamer_summary_stays_within_the_ceiling_the_mechanism_set(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway, memory_budget_tokens=TIGHT_BUDGET)
    snapshot = next(s for s in engine.run_cycle(1) if s.arm_id is ArmId.ARM_SUMMARY)
    assert snapshot.created_summary is not None
    limit = engine.configuration.dreamer_target_summary_tokens
    assert snapshot.created_summary.token_count <= limit


# ---------------------------------------------------------------- checkpoints


def test_a_checkpoint_interviews_every_arm_and_changes_nothing(pilot_engine: PilotEngine):
    before = {arm: pilot_engine.state_of(arm) for arm in pilot_engine.configuration.arms}
    records = pilot_engine.run_checkpoint(0)

    assert len(records) == 6
    assert tuple(r.arm_id for r in records) == pilot_engine.configuration.arms
    for arm, state in before.items():
        after = pilot_engine.state_of(arm)
        assert after.state_hash == state.state_hash
        assert after.memories == state.memories


def test_no_interview_answer_is_ever_admitted_to_memory(pilot_engine: PilotEngine):
    pilot_engine.run_cycle(1)
    for cycle in range(2, 13):
        pilot_engine.run_cycle(cycle)
    records = pilot_engine.run_checkpoint(12)

    answers = {answer.answer for record in records for answer in record.result.output.answers}
    assert answers
    for arm in pilot_engine.configuration.arms:
        texts = {memory.text for memory in pilot_engine.state_of(arm).memories}
        assert not (texts & answers)


def test_an_interview_does_not_move_a_citation_statistic(pilot_engine: PilotEngine):
    before = {
        m.memory_id: (m.citation_count, m.discounted_citation_score)
        for m in pilot_engine.state_of(ArmId.ARM_HEAVY).memories
    }
    pilot_engine.run_checkpoint(0)
    after = {
        m.memory_id: (m.citation_count, m.discounted_citation_score)
        for m in pilot_engine.state_of(ArmId.ARM_HEAVY).memories
    }
    assert before == after


def test_a_checkpoint_at_the_wrong_cycle_is_refused(pilot_engine: PilotEngine):
    with pytest.raises(CycleSequenceError, match="not a checkpoint"):
        pilot_engine.run_checkpoint(3)
    pilot_engine.run_cycle(1)
    with pytest.raises(CycleSequenceError, match="is not now"):
        pilot_engine.run_checkpoint(0)


def test_a_checkpoint_spends_from_its_own_allowance(pilot_engine: PilotEngine):
    pilot_engine.run_checkpoint(0)
    assert pilot_engine.budget.usage.calls_by_role == {"interviewer": 6}
    assert pilot_engine.budget.remaining(ModelRole.INTERVIEWER) == 0


# ---------------------------------------------------------- refusals at the edge


def test_a_seed_set_that_does_not_fit_stops_the_run_before_it_starts(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    base = build_run(pilot_bundle, run_id="run_tiny", gateway=pilot_gateway)
    engine = PilotEngine(
        configuration=base.configuration.model_copy(update={"memory_budget_tokens": 10}),
        bundle=pilot_bundle,
        gateway=pilot_gateway,
    )
    with pytest.raises(UnsatisfiableBudgetError, match="over the 10-token budget"):
        engine.initialize_pilot_run()


def test_a_citation_mode_the_engine_does_not_implement_is_refused(
    pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    engine = make_engine(pilot_bundle, pilot_gateway, citation_mode=CitationMode.AUDITED)
    with pytest.raises(NotImplementedError, match="not implemented by the pilot engine"):
        engine.stage_cycle(1)
    assert engine.current_cycle == 0


def test_a_mechanism_that_asks_for_a_compression_it_cannot_commit_is_refused(
    pilot_engine: PilotEngine,
):
    """The two-stage contract is a protocol, and a policy that half-implements it stops."""
    real = pilot_engine._policies[ArmId.ARM_SUMMARY]

    @dataclasses.dataclass
    class PlansButCannotCommit:
        arm_id: ArmId = ArmId.ARM_FIFO
        policy_version: str = "fifo-v1"

        def rebalance(self, state: Any, budget: Any, context: Any) -> Any:
            return real.rebalance(state, budget, context)

    pilot_engine._policies = {
        **pilot_engine._policies,
        ArmId.ARM_FIFO: PlansButCannotCommit(),
    }
    engine = pilot_engine
    engine.configuration = engine.configuration.model_copy(update={"memory_budget_tokens": 160})
    with pytest.raises(TypeError, match="cannot commit one"):
        engine.stage_cycle(1)


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("cycle", 9, "staged cycle 9"),
        ("tokens_after", 10_000, "ends over budget"),
    ],
)
def test_a_staged_result_that_does_not_match_the_cycle_is_refused(
    pilot_engine: PilotEngine, field: str, value: object, expected: str
):
    staged = pilot_engine.stage_cycle(1)
    broken = staged.results[0]
    tampered = dataclasses.replace(
        staged,
        results=(
            dataclasses.replace(broken, snapshot=broken.snapshot.model_copy(update={field: value})),
            *staged.results[1:],
        ),
    )
    with pytest.raises(ValueError, match=expected):
        pilot_engine.validate_staged_cycle(tampered)


def test_a_staged_result_on_another_stimulus_is_refused(pilot_engine: PilotEngine):
    staged = pilot_engine.stage_cycle(1)
    broken = staged.results[0]
    other = broken.snapshot.stimulus.model_copy(update={"stimulus_id": "stim_099"})
    tampered = dataclasses.replace(
        staged,
        results=(
            dataclasses.replace(
                broken, snapshot=broken.snapshot.model_copy(update={"stimulus": other})
            ),
            *staged.results[1:],
        ),
    )
    with pytest.raises(ValueError, match="received stim_099"):
        pilot_engine.validate_staged_cycle(tampered)


def test_a_staged_result_whose_hash_no_longer_matches_is_refused(pilot_engine: PilotEngine):
    staged = pilot_engine.stage_cycle(1)
    broken = staged.results[0]
    tampered = dataclasses.replace(
        staged,
        results=(
            dataclasses.replace(
                broken, snapshot=broken.snapshot.model_copy(update={"snapshot_hash": "sha256:no"})
            ),
            *staged.results[1:],
        ),
    )
    with pytest.raises(ValueError, match="hash does not match"):
        pilot_engine.validate_staged_cycle(tampered)
