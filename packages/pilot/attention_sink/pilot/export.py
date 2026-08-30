"""Writing a finished local run to a directory, and checksumming what was written.

The export is the only thing in the pilot that touches a filesystem. Everything it
writes is already immutable and already hashed; this module's job is to lay it out so
that a directory can be handed to someone else, and to leave a `checksums.sha256`
behind so they can tell whether what they received is what was produced.

A fixture run is marked in three places -- the directory's own manifest, every
snapshot's ``simulated`` flag, and the ``[simulated]`` prefix inside the text itself.
Someone must never have to recognise a fabricated run by noticing the model name.
"""

from __future__ import annotations

import hashlib
import shutil
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from attention_sink.pilot.canonical import canonical_json
from attention_sink.pilot.engine import CheckpointRecord
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.pilot.snapshots import ArmCycleSnapshot, RunSnapshot

__all__ = ["EXPORT_FILES", "ExportResult", "checksum_lines", "export_run"]

SIMULATED_NOTICE = (
    "SIMULATED RUN. Every generation in this directory was produced by a deterministic "
    "local fixture, not by a model. No figure here is a result."
)

EXPORT_FILES = (
    "run-manifest.json",
    "cycle-snapshots.jsonl",
    "arm-current-states.json",
    "checkpoints.jsonl",
    "predictions.md",
)
"""What an export always contains, besides the protocol copies and the checksums."""


@dataclass(frozen=True, slots=True)
class ExportResult:
    """Where a run was written, and what was written there."""

    directory: Path
    files: tuple[str, ...]
    """Every file written, relative to the directory, in the order checksummed."""

    simulated: bool


def _write(path: Path, text: str) -> None:
    """Write one export file, creating its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _jsonl(records: Sequence[Any]) -> str:
    """Render records one canonical JSON object per line."""
    return "".join(f"{canonical_json(record)}\n" for record in records)


def checksum_lines(directory: Path, files: Sequence[str]) -> str:
    """Render a ``sha256sum``-compatible manifest of ``files`` under ``directory``.

    The format is the one ``sha256sum -c`` reads: the digest, two spaces, and the
    path relative to the directory the file lives in. Deliberately not a bespoke
    format, so verifying an export needs no tool from this repository.
    """
    lines: list[str] = []
    for name in files:
        digest = hashlib.sha256((directory / name).read_bytes()).hexdigest()
        lines.append(f"{digest}  {name}\n")
    return "".join(lines)


def export_run(
    directory: Path,
    *,
    run: RunSnapshot,
    snapshots: Sequence[ArmCycleSnapshot],
    checkpoints: Sequence[CheckpointRecord],
    bundle: ProtocolBundle,
) -> ExportResult:
    """Write one complete local run, then checksum every file written.

    The directory is replaced rather than merged. A partial overwrite would leave
    snapshots from two runs in one place with nothing saying which cycle came from
    which, and a checksum file that covered neither.

    Raises:
        OSError: The directory could not be written.
    """
    if directory.exists():
        shutil.rmtree(directory)
    directory.mkdir(parents=True)

    simulated = run.configuration.simulated
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "simulated": simulated,
        "notice": SIMULATED_NOTICE if simulated else "",
        "run": run.model_dump(mode="json"),
        "protocol_digests": dict(bundle.digests),
        "cycle_snapshot_hashes": [snapshot.snapshot_hash for snapshot in snapshots],
        "checkpoint_cycles_run": sorted({record.cycle for record in checkpoints}),
    }
    _write(directory / "run-manifest.json", f"{canonical_json(manifest)}\n")
    _write(directory / "cycle-snapshots.jsonl", _jsonl(snapshots))
    _write(
        directory / "arm-current-states.json",
        f"{canonical_json(dict(run.arm_states))}\n",
    )
    _write(
        directory / "checkpoints.jsonl",
        _jsonl(
            [
                {
                    "run_id": record.run_id,
                    "arm_id": record.arm_id.value,
                    "cycle": record.cycle,
                    "interview_version": record.interview_version,
                    "active_memory_ids": list(record.active_memory_ids),
                    "answers": record.result.output.model_dump(mode="json"),
                    "cited_memory_ids": list(record.result.cited_memory_ids),
                    "metadata": record.result.metadata.model_dump(mode="json"),
                    "completed_at": record.completed_at.isoformat(),
                }
                for record in checkpoints
            ]
        ),
    )

    protocol_copies: list[str] = []
    for name in bundle.paths:
        target = directory / "protocol" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(bundle.root / name, target)
        protocol_copies.append(f"protocol/{name}")

    predictions = bundle.root / "predictions" / f"{bundle.protocol.protocol_version}.md"
    shutil.copyfile(predictions, directory / "predictions.md")

    files = (*EXPORT_FILES, *protocol_copies)
    _write(directory / "checksums.sha256", checksum_lines(directory, files))
    return ExportResult(directory=directory, files=files, simulated=simulated)
