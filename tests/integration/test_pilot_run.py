"""The whole pilot: twenty-four cycles locally, and the directory it leaves behind."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import NamedTuple

import pytest

from attention_sink.model_gateway import SIMULATED_PREFIX, GatewaySettings, build_gateway
from attention_sink.pilot import (
    ArmCycleSnapshot,
    CheckpointRecord,
    PilotEngine,
    ProtocolBundle,
    RunStatus,
    build_run,
    export_run,
    run_cycles,
)
from attention_sink.pilot.cli import main
from attention_sink.pilot.export import EXPORT_FILES
from tests.conftest import LOCAL_COUNTER_SOURCE, PILOT_ROOT, fixed_clock


class FullRun(NamedTuple):
    """One complete local run, kept together so a module can share it."""

    engine: PilotEngine
    snapshots: list[ArmCycleSnapshot]
    checkpoints: list[CheckpointRecord]


@pytest.fixture(scope="module")
def full_run(pilot_bundle: ProtocolBundle) -> FullRun:
    """One complete twenty-four cycle fixture run, shared by this module."""
    gateway = build_gateway(GatewaySettings.from_env(env={}))
    engine = build_run(pilot_bundle, run_id="run_full", gateway=gateway)
    engine.clock = fixed_clock
    snapshots, checkpoints = run_cycles(engine, engine.configuration.maximum_cycles)
    return FullRun(engine=engine, snapshots=snapshots, checkpoints=checkpoints)


def test_the_pilot_completes_twenty_four_cycles(full_run: FullRun):
    engine = full_run.engine
    assert engine.current_cycle == 24
    assert engine.status is RunStatus.COMPLETED
    assert len(full_run.snapshots) == 24 * 6


def test_every_cycle_is_present_exactly_once_per_arm(full_run: FullRun):
    engine = full_run.engine
    seen = {(s.arm_id, s.cycle) for s in full_run.snapshots}
    assert seen == {(arm, cycle) for arm in engine.configuration.arms for cycle in range(1, 25)}


def test_no_arm_ends_over_budget(full_run: FullRun):
    engine = full_run.engine
    budget = engine.configuration.memory_budget_tokens
    for arm in engine.configuration.arms:
        assert engine.state_of(arm).active_tokens <= budget
    assert all(s.tokens_after <= budget for s in full_run.snapshots)


def test_every_arm_forgot_something(full_run: FullRun):
    """A budget that never bound would make the whole run a test of nothing."""
    engine = full_run.engine
    for arm in engine.configuration.arms:
        state = engine.state_of(arm)
        retired = [m for m in state.memories if not m.is_active]
        assert retired, f"{arm.value} never retired a memory"


def test_the_arms_diverged(full_run: FullRun):
    engine = full_run.engine
    hashes = {engine.state_of(arm).state_hash for arm in engine.configuration.arms}
    assert len(hashes) > 1, "six mechanisms produced six identical states"


def test_the_spend_is_what_the_protocol_declared(full_run: FullRun):
    usage = full_run.engine.budget.usage
    assert usage.calls_by_role["writer"] == 24 * 6
    assert usage.calls_by_role["interviewer"] == 3 * 6
    assert usage.calls_by_role.get("evaluator") is None
    assert usage.calls_by_role.get("auditor") is None
    assert usage.total_calls <= 400
    assert usage.simulated_calls == usage.total_calls


def test_checkpoints_ran_at_zero_twelve_and_twenty_four(full_run: FullRun):
    cycles = sorted({record.cycle for record in full_run.checkpoints})
    assert cycles == [0, 12, 24]
    assert len(full_run.checkpoints) == 18


def test_every_snapshot_verifies_its_own_hash(full_run: FullRun):
    assert all(s.verify_hash() for s in full_run.snapshots)
    assert len({s.snapshot_hash for s in full_run.snapshots}) == 24 * 6


def test_everything_generated_is_marked_simulated(full_run: FullRun):
    for snapshot in full_run.snapshots:
        assert snapshot.simulated
        assert SIMULATED_PREFIX in snapshot.journal_entry
        assert all(call.simulated for call in snapshot.model_metadata)


def test_the_same_protocol_run_twice_produces_the_same_snapshots(
    pilot_bundle: ProtocolBundle, full_run: FullRun
):
    engine = build_run(
        pilot_bundle,
        run_id="run_full",
        gateway=build_gateway(GatewaySettings.from_env(env={})),
    )
    engine.clock = fixed_clock
    snapshots, _ = run_cycles(engine, 6)
    assert [s.snapshot_hash for s in snapshots] == [
        s.snapshot_hash for s in full_run.snapshots[: 6 * 6]
    ]


# ---------------------------------------------------------------------- export


@pytest.fixture(scope="module")
def exported(full_run: FullRun, tmp_path_factory: pytest.TempPathFactory) -> Path:
    directory = tmp_path_factory.mktemp("export") / "run"
    engine = full_run.engine
    export_run(
        directory,
        run=engine.run_snapshot(),
        snapshots=full_run.snapshots,
        checkpoints=full_run.checkpoints,
        bundle=engine.bundle,
    )
    return directory


def test_the_export_contains_everything_it_promises(exported: Path):
    for name in (*EXPORT_FILES, "checksums.sha256"):
        assert (exported / name).is_file(), name
    copies = sorted(p.name for p in (exported / "protocol").rglob("*.yaml"))
    assert len(copies) == 5


def test_every_exported_file_matches_its_checksum(exported: Path):
    lines = (exported / "checksums.sha256").read_text(encoding="utf-8").splitlines()
    # Five protocol documents plus the manifest that digests them.
    assert len(lines) == len(EXPORT_FILES) + 6
    for line in lines:
        digest, name = line.split("  ", 1)
        actual = hashlib.sha256((exported / name).read_bytes()).hexdigest()
        assert actual == digest, name


def test_the_manifest_says_the_run_was_simulated(exported: Path):
    manifest = json.loads((exported / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["simulated"] is True
    assert manifest["run_kind"] == "local_fixture"
    assert manifest["canonical"] is False
    assert manifest["token_count_source"] == LOCAL_COUNTER_SOURCE
    assert "SIMULATED - LOCAL - NON-CANONICAL" in manifest["notice"]
    assert len(manifest["cycle_snapshot_hashes"]) == 24 * 6
    assert manifest["checkpoint_cycles_run"] == [0, 12, 24]


def test_the_exported_snapshots_are_one_json_object_per_line(exported: Path):
    lines = (exported / "cycle-snapshots.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 24 * 6
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["cycle"] == 1
    assert parsed[-1]["cycle"] == 24


def test_the_exported_protocol_copies_are_byte_identical(exported: Path):
    for copy in (exported / "protocol").rglob("*.yaml"):
        original = PILOT_ROOT / copy.relative_to(exported / "protocol")
        assert copy.read_bytes() == original.read_bytes()


def test_exporting_twice_replaces_rather_than_merges(exported: Path, full_run: FullRun):
    stray = exported / "stray.txt"
    stray.write_text("left over", encoding="utf-8")
    engine = full_run.engine
    export_run(
        exported,
        run=engine.run_snapshot(),
        snapshots=full_run.snapshots,
        checkpoints=full_run.checkpoints,
        bundle=engine.bundle,
    )
    assert not stray.exists()


# ------------------------------------------------------------------- commands


def test_the_validate_command_reports_a_validated_protocol(capsys: pytest.CaptureFixture[str]):
    assert main(["--root", str(PILOT_ROOT), "validate"]) == 0
    out = capsys.readouterr().out
    assert "calibrated: True  local_validated: True  frozen: True" in out


def test_a_command_on_a_missing_protocol_fails_without_a_traceback(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["--root", str(tmp_path), "validate"]) == 1
    assert "FAILED:" in capsys.readouterr().err


def test_the_run_command_runs_and_exports(tmp_path: Path, capsys: pytest.CaptureFixture[str]):
    out = tmp_path / "run"
    assert main(["--root", str(PILOT_ROOT), "run", "--cycles", "2", "--out", str(out)]) == 0
    printed = capsys.readouterr().out
    assert "SIMULATED" in printed
    assert (out / "checksums.sha256").is_file()
    assert len((out / "cycle-snapshots.jsonl").read_text().splitlines()) == 12


def test_a_run_that_exports_nothing_still_runs(capsys: pytest.CaptureFixture[str]):
    assert main(["--root", str(PILOT_ROOT), "run", "--cycles", "1"]) == 0
    assert "exported" not in capsys.readouterr().out
