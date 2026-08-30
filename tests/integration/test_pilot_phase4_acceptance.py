"""One test per subject the Phase 4 brief names, in the order it names them.

Deliberately organised by the brief rather than by module. Several of these subjects
are already covered in depth elsewhere -- blindness in `test_pilot_blindness.py`,
budget arithmetic in `test_pilot_engine.py` -- and this file does not duplicate that
work. It asserts the subject itself, once, against a real twenty-four cycle local run,
so that "tests for all twenty-one listed subjects" is something a reader can check
rather than something a reviewer has to take on trust.

The whole module runs on fixture models. Nothing here reaches a network.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest

from attention_sink.domain import ArmId, MemoryKind, MemoryStatus
from attention_sink.model_gateway import ModelGateway, ModelRole
from attention_sink.pilot import (
    EXPECTED_SEED_COUNT,
    ArmCycleSnapshot,
    CheckpointRecord,
    ModelCallBudget,
    ModelCallBudgetExceeded,
    PilotEngine,
    ProtocolBundle,
    ProtocolError,
    RunKind,
    build_run,
    export_run,
    load_bundle,
    run_cycles,
)
from tests.conftest import LOCAL_COUNTER_SOURCE, PILOT_ROOT, fixed_clock

CYCLES = 24


class LocalRun(NamedTuple):
    """One complete twenty-four cycle fixture run, and everything it produced."""

    engine: PilotEngine
    snapshots: tuple[ArmCycleSnapshot, ...]
    checkpoints: tuple[CheckpointRecord, ...]


@pytest.fixture(scope="module")
def bundle() -> ProtocolBundle:
    return load_bundle(PILOT_ROOT)


@pytest.fixture(scope="module")
def local_run(bundle: ProtocolBundle) -> LocalRun:
    """The full local experiment, run once and shared by every test below."""
    engine = build_run(bundle, run_id="run_acceptance")
    engine.clock = fixed_clock
    snapshots, checkpoints = run_cycles(engine, CYCLES)
    return LocalRun(engine, tuple(snapshots), tuple(checkpoints))


def by_arm(run: LocalRun, arm_id: ArmId) -> tuple[ArmCycleSnapshot, ...]:
    """Every snapshot one arm produced, in cycle order."""
    return tuple(s for s in run.snapshots if s.arm_id is arm_id)


# ---------------------------------------------------------------- composition


def test_the_run_has_exactly_six_arms(local_run: LocalRun):
    arms = local_run.engine.configuration.arms
    assert len(arms) == 6
    assert len(set(arms)) == 6
    assert {s.arm_id for s in local_run.snapshots} == set(arms)


def test_the_seed_world_has_exactly_twelve_memories(bundle: ProtocolBundle):
    assert len(bundle.seed_world.memories) == EXPECTED_SEED_COUNT == 12


def test_the_deck_has_exactly_twenty_four_stimuli(bundle: ProtocolBundle):
    assert len(bundle.stimulus_deck.stimuli) == 24
    assert [s.cycle for s in bundle.stimulus_deck.stimuli] == list(range(1, 25))


def test_every_arm_is_initialised_identically(bundle: ProtocolBundle):
    """Same texts, same counts, same order. Only the arm-scoped ids differ."""
    engine = build_run(bundle, run_id="run_init")
    seen = {
        arm_id: tuple(
            (m.text, m.token_count, m.memory_kind) for m in engine.state_of(arm_id).active_memories
        )
        for arm_id in engine.configuration.arms
    }
    assert len(set(seen.values())) == 1
    assert len(next(iter(seen.values()))) == EXPECTED_SEED_COUNT


def test_every_arm_receives_the_same_stimulus_in_a_cycle(local_run: LocalRun):
    for cycle in range(1, CYCLES + 1):
        of_cycle = {s.stimulus for s in local_run.snapshots if s.cycle == cycle}
        assert len(of_cycle) == 1, cycle


# ------------------------------------------------------------ writer blindness


@pytest.fixture(scope="module")
def writer_prompts(bundle: ProtocolBundle) -> tuple[str, ...]:
    """Every rendered writer request of a short local run, system turn and user turn."""
    from tests.doubles import recording_gateway

    gateway, invoker = recording_gateway()
    engine = build_run(bundle, run_id="run_prompts", gateway=gateway)
    engine.clock = fixed_clock
    run_cycles(engine, 3)
    return tuple(f"{call.system}\n{call.user}" for call in invoker.calls)


@pytest.mark.parametrize(
    "forbidden",
    ["fifo", "lru", "heavy_hitter", "pinned_origin", "seeded_random", "dreamer", "arm_"],
)
def test_no_policy_name_reaches_the_writer(writer_prompts: tuple[str, ...], forbidden: str):
    assert writer_prompts
    for prompt in writer_prompts:
        assert forbidden not in prompt.lower()


@pytest.mark.parametrize("forbidden", ["The Archivist", "The Dreamer", "The Sink", "The Gambler"])
def test_no_public_character_name_reaches_the_writer(
    writer_prompts: tuple[str, ...], forbidden: str
):
    """Public arm names live in the presentation layer only (ADR-004)."""
    for prompt in writer_prompts:
        assert forbidden.lower() not in prompt.lower()


def test_no_other_arms_memories_reach_the_writer(bundle: ProtocolBundle):
    """A request offers only memories the requesting arm actually holds."""
    from attention_sink.model_gateway.rendering import parse_memory_block
    from tests.doubles import recording_gateway

    gateway, invoker = recording_gateway()
    engine = build_run(bundle, run_id="run_isolation", gateway=gateway)
    engine.clock = fixed_clock
    run_cycles(engine, 8)

    texts_by_arm = {
        arm_id: {m.text for m in engine.state_of(arm_id).memories}
        for arm_id in engine.configuration.arms
    }
    for call in invoker.calls:
        offered = {text for _, text in parse_memory_block(call.user)}
        if not offered:
            continue
        owners = [arm for arm, texts in texts_by_arm.items() if offered <= texts]
        assert owners, "a request offered text no single arm holds"


def test_no_future_stimulus_reaches_the_writer(
    bundle: ProtocolBundle, writer_prompts: tuple[str, ...]
):
    later = [s.text for s in bundle.stimulus_deck.stimuli if s.cycle > 3]
    for prompt in writer_prompts:
        for text in later:
            assert text not in prompt


def test_no_truth_ledger_metadata_reaches_the_writer(
    bundle: ProtocolBundle, writer_prompts: tuple[str, ...]
):
    """Fact identifiers, evaluator notes, and reliability labels are scoring apparatus."""
    forbidden = {fact.fact_id for fact in bundle.truth_ledger.facts}
    forbidden |= {s.evaluator_notes for s in bundle.stimulus_deck.stimuli}
    forbidden |= {s.pressure_type for s in bundle.stimulus_deck.stimuli}
    for prompt in writer_prompts:
        for token in forbidden:
            assert token not in prompt


# ------------------------------------------------------------------- citations


def test_a_citation_may_only_name_a_memory_the_arm_had_active(local_run: LocalRun):
    for snapshot in local_run.snapshots:
        offered = set(snapshot.active_memory_ids_before)
        for citation in snapshot.validated_citations:
            assert citation.memory_id in offered


# ------------------------------------------------------------------- atomicity


def test_a_cycle_commits_all_six_arms_or_none(local_run: LocalRun):
    for cycle in range(1, CYCLES + 1):
        assert len([s for s in local_run.snapshots if s.cycle == cycle]) == 6


def test_one_failing_arm_prevents_every_arm_from_advancing(
    bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """The scientific invariant that costs the most to get wrong."""
    import dataclasses

    from tests.integration.test_pilot_engine import FailOnNthWriter

    engine = build_run(bundle, run_id="run_atomic", gateway=pilot_gateway)
    engine.clock = fixed_clock
    before = {arm: engine.state_of(arm).state_hash for arm in engine.configuration.arms}

    engine.gateway = dataclasses.replace(
        pilot_gateway, writer=FailOnNthWriter(pilot_gateway.writer, fail_on=4)
    )
    with pytest.raises(RuntimeError, match="writer call 4 failed"):
        engine.run_cycle(1)

    assert engine.current_cycle == 0
    assert {arm: engine.state_of(arm).state_hash for arm in engine.configuration.arms} == before


# ---------------------------------------------------------------------- budget


def test_every_arm_stays_within_the_provisional_budget(local_run: LocalRun):
    budget = local_run.engine.configuration.memory_budget_tokens
    for snapshot in local_run.snapshots:
        assert snapshot.tokens_after <= budget
        assert snapshot.budget_tokens == budget


def test_the_pinned_memory_survives_every_cycle(local_run: LocalRun):
    pinned = local_run.engine.configuration.pinned_origin_memory_id
    for snapshot in by_arm(local_run, ArmId.ARM_SINK):
        assert pinned in snapshot.active_memory_ids_after


def test_a_retired_memory_never_returns_to_the_active_set(local_run: LocalRun):
    for arm_id in local_run.engine.configuration.arms:
        gone: set[str] = set()
        for snapshot in by_arm(local_run, arm_id):
            assert not gone & set(snapshot.active_memory_ids_after)
            gone |= {record.memory_id for record in snapshot.retired_memories}


def test_the_random_arm_is_reproducible_from_its_recorded_seed(bundle: ProtocolBundle):
    """Same seed, same evictions. Recorded provenance, not chance."""
    first = build_run(bundle, run_id="run_random_a")
    first.clock = fixed_clock
    snaps_a, _ = run_cycles(first, 12)
    second = build_run(bundle, run_id="run_random_a")
    second.clock = fixed_clock
    snaps_b, _ = run_cycles(second, 12)

    def retired(snapshots: list[ArmCycleSnapshot]) -> list[tuple[int, tuple[str, ...]]]:
        return [
            (s.cycle, tuple(r.memory_id for r in s.retired_memories))
            for s in snapshots
            if s.arm_id is ArmId.ARM_RANDOM
        ]

    assert retired(snaps_a) == retired(snaps_b)
    assert any(events for _, events in retired(snaps_a))


# --------------------------------------------------------------------- Dreamer


def test_every_dreamer_summary_keeps_its_lineage(local_run: LocalRun):
    summaries = [s for s in by_arm(local_run, ArmId.ARM_SUMMARY) if s.created_summary is not None]
    assert summaries, "the summarising arm never compressed anything"
    for snapshot in summaries:
        summary = snapshot.created_summary
        assert summary is not None
        assert summary.memory_kind is MemoryKind.SUMMARY
        assert len(summary.parent_memory_ids) >= 2
        assert set(summary.parent_memory_ids) == set(snapshot.summary_source_memory_ids)
        assert set(summary.parent_memory_ids) <= set(snapshot.compressed_memory_ids)
        compressed = {
            r.memory_id for r in snapshot.retired_memories if r.status is MemoryStatus.COMPRESSED
        }
        assert set(summary.parent_memory_ids) <= compressed


def test_a_dreamer_summary_costs_the_same_budget_as_any_other_memory(local_run: LocalRun):
    budget = local_run.engine.configuration.memory_budget_tokens
    for snapshot in by_arm(local_run, ArmId.ARM_SUMMARY):
        if snapshot.created_summary is None:
            continue
        assert snapshot.created_summary.memory_id in snapshot.active_memory_ids_after
        assert snapshot.tokens_after <= budget


def test_only_the_summarising_arm_ever_calls_the_dreamer(local_run: LocalRun):
    for entry in local_run.engine.budget.usage.ledger:
        if entry.operation == ModelRole.SUMMARIZER.value:
            assert entry.arm_id == ArmId.ARM_SUMMARY.value


# ---------------------------------------------------------------- call budgets


def test_a_normal_cycle_spends_six_writers_and_nothing_it_should_not(local_run: LocalRun):
    ledger = local_run.engine.budget.usage.ledger
    for cycle in range(1, CYCLES + 1):
        of_cycle = [e for e in ledger if e.cycle == cycle and not e.checkpoint]
        operations = [e.operation for e in of_cycle]
        assert operations.count(ModelRole.WRITER.value) == 6
        assert operations.count(ModelRole.SUMMARIZER.value) <= 2
        assert ModelRole.EVALUATOR.value not in operations
        assert ModelRole.AUDITOR.value not in operations


@pytest.mark.parametrize(
    ("role", "limit_field", "arm"),
    [
        (ModelRole.WRITER, "writer_calls_per_cycle", "arm_fifo"),
        (ModelRole.SUMMARIZER, "summary_calls_per_cycle", "arm_summary"),
    ],
)
def test_a_call_past_the_limit_is_refused_before_the_gateway_is_touched(
    bundle: ProtocolBundle, role: ModelRole, limit_field: str, arm: str
):
    """A fresh budget, not the shared run's: spending against that would falsify its ledger."""
    budget = ModelCallBudget(limits=bundle.protocol.model_call_limits, run_id="run_limits")
    budget.open_cycle(1)
    for _ in range(getattr(budget.limits, limit_field)):
        budget.spend(role, arm_id=arm)
    assert budget.remaining(role) == 0
    with pytest.raises(ModelCallBudgetExceeded, match=role.value):
        budget.spend(role, arm_id=arm)
    assert len(budget.usage.ledger) == getattr(budget.limits, limit_field)


def test_usage_is_attributed_to_a_run_a_cycle_an_arm_and_an_operation(local_run: LocalRun):
    usage = local_run.engine.budget.usage
    assert usage.total_calls == len(usage.ledger)
    assert {e.run_id for e in usage.ledger} == {"run_acceptance"}
    writers = [e for e in usage.ledger if e.operation == ModelRole.WRITER.value]
    assert len(writers) == 6 * CYCLES
    assert {e.arm_id for e in writers} == {arm.value for arm in local_run.engine.configuration.arms}
    interviews = [e for e in usage.ledger if e.operation == ModelRole.INTERVIEWER.value]
    assert {e.cycle for e in interviews} == {0, 12, 24}
    assert all(e.checkpoint for e in interviews)


# ------------------------------------------------------------------ snapshots


def test_every_snapshot_hash_is_stable_and_self_verifying(local_run: LocalRun):
    for snapshot in local_run.snapshots:
        assert snapshot.verify_hash()
        assert snapshot.sealed().snapshot_hash == snapshot.snapshot_hash


def test_two_identical_runs_produce_identical_snapshot_hashes(bundle: ProtocolBundle):
    def hashes() -> list[str]:
        engine = build_run(bundle, run_id="run_determinism")
        engine.clock = fixed_clock
        snapshots, _ = run_cycles(engine, 4)
        return [s.snapshot_hash for s in snapshots]

    assert hashes() == hashes()


def test_the_full_twenty_four_cycle_fixture_run_completes(local_run: LocalRun):
    engine = local_run.engine
    assert engine.current_cycle == CYCLES
    assert len(local_run.snapshots) == 6 * CYCLES
    assert engine.run_snapshot().verify_hash()


# ----------------------------------------------------------------- interviews


def test_an_interview_never_becomes_a_memory(local_run: LocalRun):
    answers = {
        answer.answer for record in local_run.checkpoints for answer in record.result.output.answers
    }
    for arm_id in local_run.engine.configuration.arms:
        held = {m.text for m in local_run.engine.state_of(arm_id).memories}
        assert not held & answers


def test_interviews_run_at_the_three_configured_checkpoints(local_run: LocalRun):
    assert {record.cycle for record in local_run.checkpoints} == {0, 12, 24}
    assert len(local_run.checkpoints) == 6 * 3


def test_the_interview_asks_the_ten_fixed_questions_in_order(bundle: ProtocolBundle):
    questions = bundle.interview.questions
    assert [q.question_id for q in questions] == [f"q{n:02d}" for n in range(1, 11)]
    assert [q.factual_recall for q in questions] == [True] * 6 + [False] * 4


# ------------------------------------------- protocol status and modification


def test_the_committed_protocol_still_runs_locally_after_being_frozen(bundle: ProtocolBundle):
    """Phase 4's acceptance was that a validated protocol runs a fixture pilot.

    Phase 8 froze the same files. `is_local_validated` asks whether every document
    may take part in a fixture run, which a frozen one may -- so this acceptance
    still holds, and holds for a stricter document than the one it was written for.
    """
    from attention_sink.pilot import ProtocolStatus

    assert bundle.is_local_validated
    assert bundle.is_frozen
    assert bundle.protocol.status is ProtocolStatus.FROZEN


def test_editing_a_validated_protocol_is_detected(tmp_path: Path):
    import shutil

    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    deck = root / "stimuli.yaml"
    deck.write_text(deck.read_text().replace("cold iron", "warm iron"), encoding="utf-8")

    edited = load_bundle(root)
    assert edited.drifted() == ("stimuli.yaml",)
    with pytest.raises(ProtocolError, match="modified after validation"):
        edited.require_runnable()


def test_the_manifest_detects_an_edited_file(tmp_path: Path, pilot_gateway: ModelGateway):
    import shutil

    from attention_sink.pilot import manifest_drift

    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    ledger = root / "truth_ledger.yaml"
    ledger.write_text(ledger.read_text().replace("Mara Venn", "Mara Vann"), encoding="utf-8")

    prompts = pilot_gateway.prompts
    version = load_bundle(root).protocol.writer_prompt_version
    hashes = {t.identifier: t.digest for t in prompts.manifest(version)} | {
        "prompt_set": prompts.prompt_set_digest(version)
    }
    assert "truth_ledger.yaml" in manifest_drift(load_bundle(root), prompt_hashes=hashes)


# ----------------------------------------------------- non-canonical labelling


def test_every_local_artefact_says_it_is_simulated_and_non_canonical(local_run: LocalRun):
    configuration = local_run.engine.configuration
    assert configuration.run_kind is RunKind.LOCAL_FIXTURE
    assert not configuration.canonical
    assert configuration.simulated
    assert configuration.token_count_source == LOCAL_COUNTER_SOURCE
    for snapshot in local_run.snapshots:
        assert snapshot.run_kind is RunKind.LOCAL_FIXTURE
        assert snapshot.simulated


def test_the_export_marks_the_run_non_canonical(local_run: LocalRun, tmp_path: Path):
    result = export_run(
        tmp_path / "run",
        run=local_run.engine.run_snapshot(),
        snapshots=local_run.snapshots,
        checkpoints=local_run.checkpoints,
        bundle=local_run.engine.bundle,
    )
    assert result.simulated
    manifest = json.loads((result.directory / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_kind"] == RunKind.LOCAL_FIXTURE.value
    assert manifest["canonical"] is False
    assert "NON-CANONICAL" in manifest["notice"]


def test_a_local_run_cannot_be_relabelled_canonical(bundle: ProtocolBundle):
    """The protocol is frozen now, so the refusal comes from the gateway instead.

    A fixture gateway fabricates its generations, and a canonical run may not be
    denominated in the heuristic counter that gateway builds. Both are checked by
    `require_run_kind_consistent`, before a cycle can spend anything.
    """
    with pytest.raises(ValueError, match="simulated|approximation"):
        build_run(bundle, run_id="run_nope", run_kind=RunKind.AWS_CANONICAL)
