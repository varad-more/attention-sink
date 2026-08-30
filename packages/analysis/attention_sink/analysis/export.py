"""The dataset export: everything a run produced, laid out for somebody else.

Seventeen files, written to the local filesystem or to S3 behind the same call. Every
file is built in memory first and handed to a :class:`ExportStorage` as one map, for
two reasons. The digests are then taken over what was produced rather than over what
was read back, so a truncated write is a checksum failure rather than a checksum of a
truncation. And a destination gets one call, so "replace the directory" and "refuse to
overwrite an immutable prefix" are each one decision in one place instead of a rule
spread across sixteen writes.

The destination is replaced rather than merged, because a partial overwrite leaves two
runs' records in one place with nothing saying which is which and a checksum file that
covers neither.

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
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from attention_sink.analysis.graveyard import lineage_of
from attention_sink.analysis.service import AnalysisResult
from attention_sink.pilot import ArmCycleSnapshot, canonical_json
from attention_sink.pilot.configuration import EXACT_TOKEN_COUNT_SOURCES
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.repositories import (
    ExportManifestRecord,
    PilotRepository,
    RunRecord,
)

__all__ = [
    "EXPORT_FILES",
    "EXPORT_LABELS",
    "ExportResult",
    "ExportStorage",
    "LocalExportStorage",
    "export_dataset",
    "export_labels",
]

EXPORT_LABELS: tuple[str, ...] = ("LOCAL_FIXTURE", "NON_CANONICAL", "SIMULATED_MODEL_OUTPUTS")
"""What every artefact of a local fixture run is, said three ways so none is missed.

Kept as the local run's labels because that is what every local export carries.
A deployed run's labels are :func:`export_labels`, derived from the run itself --
stamping these on a run driven by real models would be the exact failure the labels
exist to prevent, and it is one the first deployed export actually made.
"""


def export_labels(run: RunRecord) -> tuple[str, ...]:
    """What this run's artefacts are, in four independent statements.

    Derived from the run, never assumed. Each label answers a different question a
    reader will have, and each can be wrong on its own: what kind of run this was,
    whether its output may be presented as a result, whether a model or a fixture
    wrote it, and what unit its budget is denominated in.

    The last one is here because a budget counted approximately is not comparable
    with one counted by the model's own tokeniser (ADR-012), and an export that did
    not say so would invite exactly that comparison.
    """
    configuration = run.configuration
    return (
        run.run_kind.value.upper(),
        "CANONICAL" if configuration.canonical else "NON_CANONICAL",
        "SIMULATED_MODEL_OUTPUTS" if configuration.simulated else "REAL_MODEL_OUTPUTS",
        "EXACT_TOKEN_BUDGET"
        if configuration.token_count_source in EXACT_TOKEN_COUNT_SOURCES
        else "APPROXIMATE_TOKEN_BUDGET",
    )


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


@runtime_checkable
class ExportStorage(Protocol):
    """Somewhere a complete export can be put, in one call.

    Deliberately not a file-like interface. An export is atomic in the sense that
    matters -- either the whole set lands or the destination is left alone -- and a
    protocol with ``open`` and ``write`` on it would invite a caller to stream half
    of one.
    """

    @property
    def location(self) -> str:
        """Where this storage writes, recorded verbatim in the manifest."""
        ...

    def write(self, files: Mapping[str, bytes]) -> None:
        """Put every file, replacing whatever was there.

        Raises:
            OSError: The destination could not be written.
        """
        ...


@dataclass(frozen=True, slots=True)
class LocalExportStorage:
    """An export written to a directory on this machine."""

    directory: Path

    @property
    def location(self) -> str:
        """The directory, as a string, for the manifest."""
        return str(self.directory)

    def write(self, files: Mapping[str, bytes]) -> None:
        """Replace the directory with exactly these files.

        Replaced rather than merged: a leftover file from an earlier export is a
        record of a different run sitting in this one's directory, and the checksum
        file would not cover it.

        Raises:
            OSError: The directory could not be written.
        """
        if self.directory.exists():
            shutil.rmtree(self.directory)
        self.directory.mkdir(parents=True)
        for name, data in files.items():
            path = self.directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Where a run was written, and what was written there."""

    directory: Path
    """The local directory, when the destination was one. Kept as a ``Path`` because
    every local caller uses it as one; the manifest carries the general location."""

    files: tuple[str, ...]
    manifest: ExportManifestRecord


def _jsonl(records: Sequence[Any]) -> str:
    return "".join(f"{canonical_json(record)}\n" for record in records)


def _document(payload: dict[str, Any]) -> bytes:
    """One canonical-JSON file, newline-terminated, as bytes."""
    return (canonical_json(payload) + "\n").encode("utf-8")


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
    destination: Path | ExportStorage,
    *,
    run: RunRecord,
    repository: PilotRepository,
    bundle: ProtocolBundle,
    analysis: AnalysisResult,
    export_id: str | None = None,
    now: datetime | None = None,
) -> ExportResult:
    """Build the complete dataset for one run, checksum it, and put it somewhere.

    Args:
        destination: Where to write. A ``Path`` is a local directory; anything else
            must satisfy :class:`ExportStorage`. The ``Path`` form is kept because
            every local caller has one and wrapping it at each call site would say
            nothing.
        run: The run being exported.
        repository: The store to read the run out of. Never written to except for the
            one manifest this export records.
        bundle: The protocol the run was defined by.
        analysis: The pass whose conclusions are exported alongside the records.
        export_id: Identifier for this export. Defaults to a timestamp.
        now: The moment recorded on the manifest. Defaults to now.

    Raises:
        OSError: The destination could not be written.
    """
    storage = LocalExportStorage(destination) if isinstance(destination, Path) else destination
    stamped = now or datetime.now(UTC)
    identifier = export_id or f"export-{stamped.strftime('%Y%m%dT%H%M%S')}"

    snapshots = _all_snapshots(repository, run)
    interviews = repository.get_interviews(run.run_id)
    metrics = repository.get_metrics(run.run_id)
    states = repository.get_all_current_arm_states(run.run_id)

    labels = {"labels": list(export_labels(run))}
    files: dict[str, bytes] = {
        "run-manifest.json": _document(
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
        ),
        "protocol.json": _document({**labels, "protocol": bundle.protocol.model_dump(mode="json")}),
        "seed-memories.json": _document(
            {**labels, "seed_world": bundle.seed_world.model_dump(mode="json")}
        ),
        "stimuli.json": _document({**labels, "deck": bundle.stimulus_deck.model_dump(mode="json")}),
        "predictions.md": (bundle.root / "predictions.md").read_bytes(),
        "cycle-snapshots.jsonl": _jsonl(snapshots).encode("utf-8"),
        "arm-current-states.json": _document({**labels, "arm_states": states}),
        "graveyard.jsonl": _jsonl(analysis.graveyard).encode("utf-8"),
        "interviews.jsonl": _jsonl(interviews).encode("utf-8"),
        "metrics.jsonl": _jsonl(metrics).encode("utf-8"),
        "metrics.csv": _csv(
            ("run_id", "arm_id", "cycle", "metric_name", "value", "calculation_version"),
            [
                (m.run_id, m.arm_id.value, m.cycle, m.metric_name, m.value, m.calculation_version)
                for m in metrics
            ],
        ).encode("utf-8"),
        "divergence-matrices.json": _document({**labels, "matrices": analysis.divergence}),
        "model-usage.csv": _csv(
            ("run_id", "cycle", "arm_id", "operation", "checkpoint"),
            [
                (e.run_id, e.cycle, e.arm_id or "", e.operation, int(e.checkpoint))
                for e in run.usage.ledger
            ],
        ).encode("utf-8"),
        "lineage.json": _document({**labels, "lineage": _lineage(snapshots)}),
        "prompt-manifest.json": _document(
            {
                **labels,
                "prompt_set_digest": run.configuration.prompt_set_digest,
                "writer_prompt_version": str(run.configuration.writer_prompt_version),
                "summary_prompt_version": str(run.configuration.summary_prompt_version),
                "prompt_hashes": _prompt_hashes(snapshots),
            }
        ),
    }

    manifest = ExportManifestRecord(
        run_id=run.run_id,
        export_id=identifier,
        run_kind=run.run_kind,
        directory=storage.location,
        files={},
        labels=export_labels(run),
        created_at=stamped,
    )
    files["export-manifest.json"] = _document(
        {**labels, "manifest": manifest.model_dump(mode="json")}
    )

    # Over what was produced, not over what was read back. A digest taken after a
    # truncated write would be a correct digest of a truncated file.
    digests = {name: hashlib.sha256(files[name]).hexdigest() for name in EXPORT_FILES}
    files["checksums.sha256"] = "".join(
        f"{digest}  {name}\n" for name, digest in digests.items()
    ).encode("utf-8")

    storage.write(files)
    stored = repository.store_export_manifest(manifest.model_copy(update={"files": digests}))
    local = destination if isinstance(destination, Path) else Path(storage.location)
    return ExportResult(directory=local, files=EXPORT_FILES, manifest=stored)


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
