"""The dataset export: everything a run produced, laid out for somebody else.

Seventeen files, written to the local filesystem in Phases 5-6 and to S3 in Phase 7
behind the same call. The directory is replaced rather than merged, because a partial
overwrite leaves two runs' records in one place with nothing saying which is which and
a checksum file that covers neither.

Three labels are stamped on the manifest and repeated in every JSON line that carries
a generation: ``LOCAL_FIXTURE``, ``NON_CANONICAL``, ``SIMULATED_MODEL_OUTPUTS``. An
export is the artefact most likely to be read by someone who was not here when it was
produced, and the one place where a missing provenance mark becomes a false result.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from attention_sink.analysis.graveyard import lineage_of
from attention_sink.analysis.service import AnalysisResult
from attention_sink.pilot import ArmCycleSnapshot, canonical_json
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.repositories import (
    ExportManifestRecord,
    PilotRepository,
    RunRecord,
)

__all__ = ["EXPORT_FILES", "EXPORT_LABELS", "ExportResult", "export_dataset"]

EXPORT_LABELS: tuple[str, ...] = ("LOCAL_FIXTURE", "NON_CANONICAL", "SIMULATED_MODEL_OUTPUTS")
"""What every artefact of a local run is, said three ways so none can be missed."""

EXPORT_FILES: tuple[str, ...] = (
    "run-manifest.json",
    "protocol.json",
    "seed-memories.json",
    "stimuli.json",
    "predictions.md",
    "cycle-snapshots.jsonl",
    "arm-current-states.json",
    "graveyard.jsonl",
    "interviews.jsonl",
    "metrics.jsonl",
    "metrics.csv",
    "divergence-matrices.json",
    "model-usage.csv",
    "lineage.json",
    "prompt-manifest.json",
    "export-manifest.json",
)
"""Every file an export writes, besides ``checksums.sha256``, in written order."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Where a run was written, and what was written there."""

    directory: Path
    files: tuple[str, ...]
    manifest: ExportManifestRecord


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(records: Sequence[Any]) -> str:
    return "".join(f"{canonical_json(record)}\n" for record in records)


def _csv(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    """Render a CSV with a stable header and LF endings.

    LF rather than the module default CRLF, so a checksum computed on one machine
    verifies on another.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    return buffer.getvalue()


def export_dataset(
    directory: Path,
    *,
    run: RunRecord,
    repository: PilotRepository,
    bundle: ProtocolBundle,
    analysis: AnalysisResult,
    export_id: str | None = None,
    now: datetime | None = None,
) -> ExportResult:
    """Write the complete dataset for one run, then checksum every file.

    Raises:
        OSError: The directory could not be written.
    """
    stamped = now or datetime.now(UTC)
    identifier = export_id or f"export-{stamped.strftime('%Y%m%dT%H%M%S')}"
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    snapshots = _all_snapshots(repository, run)
    interviews = repository.get_interviews(run.run_id)
    metrics = repository.get_metrics(run.run_id)
    states = repository.get_all_current_arm_states(run.run_id)

    labels = {"labels": list(EXPORT_LABELS)}
    _write(
        directory / "run-manifest.json",
        canonical_json(
            {
                **labels,
                "schema_version": 1,
                "run_id": run.run_id,
                "run_kind": run.run_kind.value,
                "status": run.status.value,
                "current_cycle": run.current_cycle,
                "configuration": run.configuration.model_dump(mode="json"),
                "usage": run.usage.model_dump(mode="json"),
                "completed_cycles": list(repository.list_completed_cycles(run.run_id)),
                "exported_at": stamped.isoformat(),
            }
        )
        + "\n",
    )
    _write(
        directory / "protocol.json",
        canonical_json({**labels, "protocol": bundle.protocol.model_dump(mode="json")}) + "\n",
    )
    _write(
        directory / "seed-memories.json",
        canonical_json({**labels, "seed_world": bundle.seed_world.model_dump(mode="json")}) + "\n",
    )
    _write(
        directory / "stimuli.json",
        canonical_json({**labels, "deck": bundle.stimulus_deck.model_dump(mode="json")}) + "\n",
    )
    shutil.copyfile(bundle.root / "predictions.md", directory / "predictions.md")

    _write(directory / "cycle-snapshots.jsonl", _jsonl(snapshots))
    _write(
        directory / "arm-current-states.json",
        canonical_json({**labels, "arm_states": states}) + "\n",
    )
    _write(directory / "graveyard.jsonl", _jsonl(analysis.graveyard))
    _write(directory / "interviews.jsonl", _jsonl(interviews))
    _write(directory / "metrics.jsonl", _jsonl(metrics))
    _write(
        directory / "metrics.csv",
        _csv(
            ("run_id", "arm_id", "cycle", "metric_name", "value", "calculation_version"),
            [
                (m.run_id, m.arm_id.value, m.cycle, m.metric_name, m.value, m.calculation_version)
                for m in metrics
            ],
        ),
    )
    _write(
        directory / "divergence-matrices.json",
        canonical_json({**labels, "matrices": analysis.divergence}) + "\n",
    )
    _write(
        directory / "model-usage.csv",
        _csv(
            ("run_id", "cycle", "arm_id", "operation", "checkpoint"),
            [
                (e.run_id, e.cycle, e.arm_id or "", e.operation, int(e.checkpoint))
                for e in run.usage.ledger
            ],
        ),
    )
    _write(
        directory / "lineage.json",
        canonical_json({**labels, "lineage": _lineage(snapshots)}) + "\n",
    )
    _write(
        directory / "prompt-manifest.json",
        canonical_json(
            {
                **labels,
                "prompt_set_digest": run.configuration.prompt_set_digest,
                "writer_prompt_version": str(run.configuration.writer_prompt_version),
                "summary_prompt_version": str(run.configuration.summary_prompt_version),
                "prompt_hashes": _prompt_hashes(snapshots),
            }
        )
        + "\n",
    )

    manifest = ExportManifestRecord(
        run_id=run.run_id,
        export_id=identifier,
        run_kind=run.run_kind,
        directory=str(directory),
        files={},
        labels=EXPORT_LABELS,
        created_at=stamped,
    )
    _write(
        directory / "export-manifest.json",
        canonical_json({**labels, "manifest": manifest.model_dump(mode="json")}) + "\n",
    )

    digests = {
        name: hashlib.sha256((directory / name).read_bytes()).hexdigest() for name in EXPORT_FILES
    }
    _write(
        directory / "checksums.sha256",
        "".join(f"{digest}  {name}\n" for name, digest in digests.items()),
    )
    stored = repository.store_export_manifest(manifest.model_copy(update={"files": digests}))
    return ExportResult(directory=directory, files=EXPORT_FILES, manifest=stored)


def _all_snapshots(repository: PilotRepository, run: RunRecord) -> tuple[ArmCycleSnapshot, ...]:
    """Every committed snapshot, in cycle then configured-arm order.

    Configured arm order rather than alphabetical, so two runs of one protocol export
    byte-identical files.
    """
    ordered: list[ArmCycleSnapshot] = []
    for cycle in repository.list_completed_cycles(run.run_id):
        by_arm = {s.arm_id: s for s in repository.list_cycle_snapshots(run.run_id, cycle=cycle)}
        ordered.extend(by_arm[arm] for arm in run.configuration.arms if arm in by_arm)
    return tuple(ordered)


def _lineage(snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, dict[str, list[str]]]:
    """Parents and children for every summary and every memory one absorbed."""
    by_arm: dict[str, list[ArmCycleSnapshot]] = {}
    for snapshot in snapshots:
        by_arm.setdefault(snapshot.arm_id.value, []).append(snapshot)
    lineage: dict[str, dict[str, list[str]]] = {}
    for arm, arm_snapshots in by_arm.items():
        interesting = {
            memory_id
            for snapshot in arm_snapshots
            if snapshot.created_summary is not None
            for memory_id in (
                snapshot.created_summary.memory_id,
                *snapshot.created_summary.parent_memory_ids,
            )
        }
        for memory_id in sorted(interesting):
            lineage[f"{arm}:{memory_id}"] = lineage_of(memory_id, arm_snapshots)
    return lineage


def _prompt_hashes(snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, str]:
    """Every distinct prompt digest the run's snapshots recorded."""
    hashes: dict[str, str] = {}
    for snapshot in snapshots:
        hashes.update(snapshot.prompt_hashes)
    return dict(sorted(hashes.items()))


def verify_checksums(directory: Path) -> tuple[str, ...]:
    """Every file whose bytes no longer match the recorded digest.

    Raises:
        FileNotFoundError: There is no checksum file to verify against.
    """
    manifest = directory / "checksums.sha256"
    failures: list[str] = []
    for line in manifest.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        path = directory / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != digest:
            failures.append(name)
    return tuple(failures)


def read_jsonl(path: Path) -> tuple[dict[str, Any], ...]:
    """Parse a JSON-lines file written by this module."""
    return tuple(
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    )
