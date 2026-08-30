"""The pilot commands: calibrate, freeze, validate, and how each one refuses."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from attention_sink.domain import HeuristicTokenCounter
from attention_sink.model_gateway import GatewaySettings, build_gateway
from attention_sink.pilot import ProtocolError, ProtocolStatus, calibrate, load_bundle, main
from attention_sink.pilot.cli import _rewrite_nested, proposed_budget
from attention_sink.pilot.protocol import read_document
from tests.conftest import PILOT_ROOT


@pytest.fixture
def draft(tmp_path: Path) -> Path:
    """A writable copy of the protocol, returned to an uncalibrated draft."""
    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    for relative in load_bundle(root).paths:
        _blank(root / relative)
    return root


def _blank(path: Path) -> None:
    """Return one file to the state it is committed in before calibration."""
    lines = []
    for line in path.read_text(encoding="utf-8").splitlines(keepends=True):
        key = line.split(":", 1)[0].strip().lstrip("- ")
        indent = line[: len(line) - len(line.lstrip())]
        if line.strip().startswith("status:"):
            lines.append(f"{indent}status: draft\n")
        elif line.strip().startswith("content_hash:"):
            lines.append(f'{indent}content_hash: ""\n')
        elif key in {"token_count", "counter_version", "memory_budget_tokens"} and (
            line.strip().startswith(f"{key}:")
        ):
            lines.append(f"{indent}{key}: null\n")
        else:
            lines.append(line)
    path.write_text("".join(lines), encoding="utf-8")


# ----------------------------------------------------------------- calibration


def test_a_draft_starts_uncalibrated(draft: Path):
    bundle = load_bundle(draft)
    assert not bundle.is_frozen
    assert not bundle.protocol.is_calibrated
    assert not bundle.seed_world.is_calibrated
    with pytest.raises(ProtocolError, match="has not been calibrated"):
        assert bundle.seed_world.total_tokens


def test_calibration_writes_counts_hashes_and_a_budget(draft: Path):
    counter = HeuristicTokenCounter()
    total, budget = calibrate(load_bundle(draft), counter)  # type: ignore[arg-type]

    assert budget == proposed_budget(total)
    bundle = load_bundle(draft)
    assert bundle.seed_world.is_calibrated
    assert bundle.protocol.is_calibrated
    assert bundle.seed_world.total_tokens == total
    assert bundle.protocol.memory_budget_tokens == budget
    assert bundle.protocol.counter_version == counter.version
    for seed in bundle.seed_world.memories:
        assert seed.token_count == counter.count(seed.text)
        assert seed.content_hash == seed.expected_content_hash
    for stimulus in bundle.stimulus_deck.stimuli:
        assert stimulus.content_hash == stimulus.expected_content_hash


def test_calibration_is_idempotent(draft: Path):
    counter = HeuristicTokenCounter()
    first = calibrate(load_bundle(draft), counter)  # type: ignore[arg-type]
    before = (draft / "protocols/pilot-v1.yaml").read_bytes()
    second = calibrate(load_bundle(draft), counter)  # type: ignore[arg-type]
    assert first == second
    assert (draft / "protocols/pilot-v1.yaml").read_bytes() == before


def test_calibration_preserves_the_comments_a_reviewer_reads(draft: Path):
    before = (draft / "seed-worlds/station-kestrel-pilot-v1.yaml").read_text().count("#")
    calibrate(load_bundle(draft), HeuristicTokenCounter())  # type: ignore[arg-type]
    after = (draft / "seed-worlds/station-kestrel-pilot-v1.yaml").read_text().count("#")
    assert after == before > 0


def test_a_field_missing_from_a_list_item_is_refused(tmp_path: Path):
    path = tmp_path / "items.yaml"
    path.write_text("items:\n  - memory_id: seed_01\n    text: a\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="no field to write for"):
        _rewrite_nested(path, anchor="memory_id", values={"seed_01": {"token_count": "3"}})


def test_a_list_item_the_file_does_not_have_is_refused(tmp_path: Path):
    path = tmp_path / "items.yaml"
    path.write_text("items:\n  - memory_id: seed_01\n    token_count: null\n", encoding="utf-8")
    with pytest.raises(ProtocolError, match="seed_02"):
        _rewrite_nested(path, anchor="memory_id", values={"seed_02": {"token_count": "3"}})


# -------------------------------------------------------------------- commands


def test_calibrate_then_freeze_then_run(draft: Path, capsys: pytest.CaptureFixture[str]):
    """The whole sequence a protocol goes through, in the order it must go through it."""
    assert main(["--root", str(draft), "validate"]) == 0
    assert "calibrated: False  frozen: False" in capsys.readouterr().out

    assert main(["--root", str(draft), "calibrate"]) == 0
    out = capsys.readouterr().out
    assert "seed tokens:" in out
    assert "calibrated: True  frozen: False" in out

    assert main(["--root", str(draft), "freeze"]) == 0
    out = capsys.readouterr().out
    assert out.count("froze ") == 5
    assert "calibrated: True  frozen: True" in out

    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 0
    assert "SIMULATED" in capsys.readouterr().out


def test_freezing_twice_writes_nothing_the_second_time(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "freeze"])
    capsys.readouterr()
    assert main(["--root", str(draft), "freeze"]) == 0
    assert "already frozen" in capsys.readouterr().out


def test_freezing_an_uncalibrated_protocol_fails_cleanly(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["--root", str(draft), "freeze"]) == 1
    assert "uncalibrated" in capsys.readouterr().err


def test_running_a_draft_fails_cleanly(draft: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 1
    assert "not frozen" in capsys.readouterr().err


def test_validate_reports_a_file_edited_after_freezing(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "freeze"])
    capsys.readouterr()

    deck = draft / "stimulus-decks/station-kestrel-pilot-v1.yaml"
    deck.write_text(deck.read_text().replace("cold iron", "warm iron"), encoding="utf-8")

    assert main(["--root", str(draft), "validate"]) == 1
    captured = capsys.readouterr()
    assert "MODIFIED SINCE FREEZING" in captured.out
    assert "recomputed:" in captured.out
    assert "modified after freezing" in captured.err


def test_a_retired_protocol_is_reported_as_not_frozen(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "freeze"])
    path = draft / "protocols/pilot-v1.yaml"
    path.write_text(
        path.read_text().replace(
            f"status: {ProtocolStatus.FROZEN.value}", f"status: {ProtocolStatus.RETIRED.value}"
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["--root", str(draft), "validate"]) == 0
    assert "retired" in capsys.readouterr().out
    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 1


def test_the_run_command_can_be_told_it_is_canonical_and_refuses():
    with pytest.raises(ValueError, match="marked canonical but its models are simulated"):
        main(["--root", str(PILOT_ROOT), "run", "--cycles", "1", "--canonical"])


def test_a_seed_the_counter_cannot_measure_is_refused(draft: Path):
    class ZeroCounter(HeuristicTokenCounter):
        def count(self, text: str) -> int:  # noqa: ARG002 - a counter that measures nothing
            return 0

    with pytest.raises(ProtocolError, match="reports no tokens"):
        calibrate(load_bundle(draft), ZeroCounter())  # type: ignore[arg-type]


def test_the_gateway_the_commands_build_is_a_fixture_gateway():
    """`build_gateway` with an empty environment must never reach a provider."""
    gateway = build_gateway(GatewaySettings.from_env(env={}))
    assert gateway.simulated
    assert gateway.settings.models is None


def test_the_committed_protocol_is_exactly_what_calibration_would_write():
    """A drifted committed protocol would make every run in the repo unreproducible."""
    bundle = load_bundle(PILOT_ROOT)
    counter = HeuristicTokenCounter()
    for seed in bundle.seed_world.memories:
        assert seed.token_count == counter.count(seed.text)
    assert bundle.protocol.memory_budget_tokens == proposed_budget(bundle.seed_world.total_tokens)
    assert read_document(PILOT_ROOT / "protocols/pilot-v1.yaml")["status"] == "frozen"
