"""The pilot commands: calibrate, local-validate, draft, validate, and how each refuses."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from attention_sink.domain import HeuristicTokenCounter
from attention_sink.model_gateway import GatewaySettings, build_gateway
from attention_sink.pilot import (
    ProtocolError,
    ProtocolStatus,
    calibrate,
    load_bundle,
    main,
    read_manifest,
)
from attention_sink.pilot.cli import _rewrite_nested, proposed_budget
from attention_sink.pilot.protocol import read_document
from tests.conftest import LOCAL_COUNTER_SOURCE, PILOT_ROOT


@pytest.fixture
def draft(tmp_path: Path) -> Path:
    """A writable copy of the protocol, returned to an uncalibrated draft."""
    root = tmp_path / "pilot"
    shutil.copytree(PILOT_ROOT, root)
    for relative in load_bundle(root).paths:
        _blank(root / relative)
    # A draft has no manifest: the manifest is written by local-validate and is a
    # claim about digests a draft does not yet have.
    (root / "manifest.json").unlink(missing_ok=True)
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
        elif key in {"provisional_token_count", "counter_version", "memory_budget_tokens"} and (
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
        assert seed.provisional_token_count == counter.count(seed.text)
        assert seed.content_hash == seed.expected_content_hash
    for stimulus in bundle.stimulus_deck.stimuli:
        assert stimulus.content_hash == stimulus.expected_content_hash


def test_calibration_is_idempotent(draft: Path):
    counter = HeuristicTokenCounter()
    first = calibrate(load_bundle(draft), counter)  # type: ignore[arg-type]
    before = (draft / "protocol.yaml").read_bytes()
    second = calibrate(load_bundle(draft), counter)  # type: ignore[arg-type]
    assert first == second
    assert (draft / "protocol.yaml").read_bytes() == before


def test_calibration_preserves_the_comments_a_reviewer_reads(draft: Path):
    before = (draft / "seed_memories.yaml").read_text().count("#")
    calibrate(load_bundle(draft), HeuristicTokenCounter())  # type: ignore[arg-type]
    after = (draft / "seed_memories.yaml").read_text().count("#")
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


def test_calibrate_then_local_validate_then_run(draft: Path, capsys: pytest.CaptureFixture[str]):
    """The whole sequence a protocol goes through, in the order it must go through it."""
    assert main(["--root", str(draft), "validate"]) == 0
    assert "calibrated: False  local_validated: False" in capsys.readouterr().out

    assert main(["--root", str(draft), "calibrate"]) == 0
    out = capsys.readouterr().out
    assert "seed tokens:" in out
    assert "calibrated: True  local_validated: False" in out

    assert main(["--root", str(draft), "local-validate"]) == 0
    out = capsys.readouterr().out
    assert out.count("\nvalidated ") + out.startswith("validated ") == 5
    assert "calibrated: True  local_validated: True  frozen: False" in out
    assert "manifest" in out

    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 0
    assert "SIMULATED - LOCAL - NON-CANONICAL" in capsys.readouterr().out


def test_the_manifest_records_every_file_and_the_prompt_hashes(draft: Path):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    manifest = read_manifest(draft)
    assert set(manifest["files"]) == {*load_bundle(draft).paths, "predictions.md"}
    assert manifest["prompt_hashes"]
    assert manifest["status"] == ProtocolStatus.LOCAL_VALIDATED.value
    assert manifest["token_count_source"] == LOCAL_COUNTER_SOURCE


def test_an_edited_file_makes_the_manifest_stale(draft: Path, capsys: pytest.CaptureFixture[str]):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    # Return to draft first, so the *manifest* is what reports the edit rather than
    # the file's own digest. Both detect it; this test is about the manifest.
    main(["--root", str(draft), "draft"])
    deck = draft / "stimuli.yaml"
    deck.write_text(deck.read_text().replace("cold iron", "warm iron"), encoding="utf-8")
    main(["--root", str(draft), "local-validate"])
    capsys.readouterr()

    manifest = draft / "manifest.json"
    manifest.write_text(
        manifest.read_text().replace('"stimuli.yaml": "sha256:', '"stimuli.yaml": "sha256:0')
    )
    assert main(["--root", str(draft), "validate"]) == 1
    assert "STALE" in capsys.readouterr().out


def test_draft_returns_the_protocol_so_it_can_be_edited(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    capsys.readouterr()

    assert main(["--root", str(draft), "draft"]) == 0
    out = capsys.readouterr().out
    assert out.count("returned ") == 5
    assert "local_validated: False" in out
    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 1

    assert main(["--root", str(draft), "draft"]) == 0
    assert "already a draft" in capsys.readouterr().out


def test_validating_twice_writes_nothing_the_second_time(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    capsys.readouterr()
    assert main(["--root", str(draft), "local-validate"]) == 0
    assert "already local-validated" in capsys.readouterr().out


def test_validating_an_uncalibrated_protocol_fails_cleanly(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    assert main(["--root", str(draft), "local-validate"]) == 1
    assert "uncalibrated" in capsys.readouterr().err


def test_running_a_draft_fails_cleanly(draft: Path, capsys: pytest.CaptureFixture[str]):
    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 1
    assert "not validated" in capsys.readouterr().err


def test_validate_reports_a_file_edited_after_validation(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    capsys.readouterr()

    deck = draft / "stimuli.yaml"
    deck.write_text(deck.read_text().replace("cold iron", "warm iron"), encoding="utf-8")

    assert main(["--root", str(draft), "validate"]) == 1
    captured = capsys.readouterr()
    assert "MODIFIED SINCE VALIDATION" in captured.out
    assert "recomputed:" in captured.out
    assert "modified after validation" in captured.err


def test_a_retired_protocol_is_reported_as_not_runnable(
    draft: Path, capsys: pytest.CaptureFixture[str]
):
    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    path = draft / "protocol.yaml"
    path.write_text(
        path.read_text().replace(
            f"status: {ProtocolStatus.LOCAL_VALIDATED.value}",
            f"status: {ProtocolStatus.RETIRED.value}",
        ),
        encoding="utf-8",
    )
    capsys.readouterr()
    assert main(["--root", str(draft), "validate"]) == 0
    assert "retired" in capsys.readouterr().out
    assert main(["--root", str(draft), "run", "--cycles", "1"]) == 1


def test_the_run_command_can_be_told_it_is_canonical_and_refuses(
    capsys: pytest.CaptureFixture[str],
):
    """A canonical run needs a frozen protocol, which this phase never produces."""
    assert main(["--root", str(PILOT_ROOT), "run", "--cycles", "1", "--run-kind", "aws_canonical"])
    assert "not frozen" in capsys.readouterr().err


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
        assert seed.provisional_token_count == counter.count(seed.text)
    assert bundle.protocol.memory_budget_tokens == proposed_budget(bundle.seed_world.total_tokens)
    assert read_document(PILOT_ROOT / "protocol.yaml")["status"] == "local_validated"


# -------------------------------------------------------------------- manifest


def prompt_hashes(bundle: object) -> dict[str, str]:
    """The digests the manifest covers, resolved the way a run resolves them."""
    from attention_sink.pilot.cli import _prompt_hashes

    return _prompt_hashes(bundle)  # type: ignore[arg-type]


def test_a_missing_manifest_is_reported_rather_than_assumed_empty(draft: Path):
    from attention_sink.pilot import manifest_drift, read_manifest

    with pytest.raises(ProtocolError, match="no protocol manifest"):
        read_manifest(draft)
    bundle = load_bundle(draft)
    with pytest.raises(ProtocolError, match="no protocol manifest"):
        manifest_drift(bundle, prompt_hashes=prompt_hashes(bundle))


@pytest.mark.parametrize(
    ("content", "expected"),
    [("[]", "must contain a JSON object"), ('{"schema_version": 1}', "records no file digests")],
)
def test_a_malformed_manifest_is_refused(draft: Path, content: str, expected: str):
    from attention_sink.pilot import manifest_drift

    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    (draft / "manifest.json").write_text(content, encoding="utf-8")

    bundle = load_bundle(draft)
    with pytest.raises(ProtocolError, match=expected):
        manifest_drift(bundle, prompt_hashes=prompt_hashes(bundle))


def test_a_changed_prompt_template_makes_the_manifest_stale(draft: Path):
    """The prompts are apparatus too: a re-worded writer prompt is a protocol change."""
    from attention_sink.pilot import manifest_drift

    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])

    bundle = load_bundle(draft)
    hashes = {**prompt_hashes(bundle), "prompt_set": "sha256:something-else"}
    assert manifest_drift(bundle, prompt_hashes=hashes) == ("prompt templates",)


def test_a_file_the_manifest_does_not_know_about_is_reported(draft: Path):
    from attention_sink.pilot import manifest_drift

    main(["--root", str(draft), "calibrate"])
    main(["--root", str(draft), "local-validate"])
    path = draft / "manifest.json"
    recorded = json.loads(path.read_text(encoding="utf-8"))
    recorded["files"]["stray.yaml"] = "sha256:0"
    path.write_text(json.dumps(recorded), encoding="utf-8")

    bundle = load_bundle(draft)
    assert "stray.yaml (not in manifest)" in manifest_drift(
        bundle, prompt_hashes=prompt_hashes(bundle)
    )
