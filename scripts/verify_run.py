#!/usr/bin/env python
"""Check a persisted run against every invariant it claims to satisfy.

Reads a store and an export; writes nothing. Prints every check either way, so a
passing run is as legible as a failing one, and exits non-zero if any fails.

The checks are the scientific invariants restated as questions about stored data. A
run that passes them is not necessarily interesting; a run that fails one is not
evidence about anything.

Every question is asked through the `PilotRepository` port, so the same twenty-three
checks verify a local SQLite run and the deployed canonical one. That is the point:
a canonical run verified by a second implementation of the checks would be verified
against a second opinion about what the invariants mean.

    python scripts/verify_run.py --run-id run_local_pilot [--export DIR]
    python scripts/verify_run.py --source aws --run-id run_aws_canonical
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from attention_sink.analysis import EXPORT_FILES, build_graveyard, verify_checksums
from attention_sink.domain import MemoryKind, MemoryStatus
from attention_sink.pilot.local import DEFAULT_DATABASE, DEFAULT_RUN_ID
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT, load_bundle
from attention_sink.pilot.repositories import PilotRepository

Check = Callable[[], Iterator[str]]
"""A check yields one line per problem, and nothing at all when it passes."""

SECRET_MARKERS: tuple[str, ...] = (
    "AKIA",
    "ASIA",
    "aws_secret_access_key",
    "aws_session_token",
    "BEGIN PRIVATE KEY",
    "BEGIN RSA PRIVATE KEY",
    "Authorization:",
    "Bearer ",
    "answer_terms",
    "accepted_variants",
)
"""Strings no published dataset may contain.

The last two are not credentials: they are the truth ledger's scoring apparatus,
and publishing the words an answer must contain to count as recall would let a
later run be written against the mark scheme. See `PRIVATE_TRUTH_FIELDS`.
"""


def open_repository(source: str, database: Path) -> PilotRepository:
    """Build the store named by ``source``.

    Imported inside the branch rather than at module scope so that verifying a local
    run needs no AWS SDK and no credential, which is the property the local-first
    override asked for and the one a laptop still relies on.
    """
    if source == "local":
        from attention_sink.persistence import SqliteRepository

        return SqliteRepository(database)

    import os

    import boto3

    from attention_sink.aws.dynamodb import DynamoRepository

    table = os.environ.get("AS_TABLE_NAME", "").strip()
    if not table:
        msg = "set AS_TABLE_NAME to the deployed table (the TableName stack output)"
        raise SystemExit(msg)
    return DynamoRepository(table_name=table, client=boto3.client("dynamodb"))


def run_checks(*, repository: PilotRepository, run_id: str, root: Path, export: Path | None) -> int:
    """Run every check and report. Returns a process exit status."""
    bundle = load_bundle(root)
    run = repository.get_run(run_id)
    if run is None:
        print(f"FAILED: no run {run_id}", file=sys.stderr)
        return 1

    configuration = run.configuration
    states = repository.get_all_current_arm_states(run_id)
    completed = repository.list_completed_cycles(run_id)
    snapshots_by_arm = {
        arm: repository.list_arm_snapshots(run_id, arm_id=arm) for arm in configuration.arms
    }

    def six_arms() -> Iterator[str]:
        missing = [a.value for a in configuration.arms if a.value not in states]
        if missing:
            yield f"no stored state for {', '.join(missing)}"
        if len(configuration.arms) != 6:
            yield f"the run configures {len(configuration.arms)} arms, not six"

    def seed_states_match() -> Iterator[str]:
        """Every arm must have started from the same twelve texts."""
        firsts = {
            arm: tuple(sorted(s[0].active_memory_ids_before)) if s else ()
            for arm, s in snapshots_by_arm.items()
        }
        counts = {len(ids) for ids in firsts.values()}
        if len(counts) > 1:
            yield f"arms began cycle 1 holding different numbers of memories: {counts}"
        expected = len(bundle.seed_world.memories)
        if counts and counts != {expected}:
            yield f"arms began with {counts} memories; the seed world has {expected}"

    def one_stimulus_per_cycle() -> Iterator[str]:
        for cycle in completed:
            stimuli = {
                s.stimulus.stimulus_id for s in repository.list_cycle_snapshots(run_id, cycle=cycle)
            }
            if len(stimuli) != 1:
                yield f"cycle {cycle} used {len(stimuli)} stimuli: {sorted(stimuli)}"
            expected = bundle.stimulus_deck.for_cycle(cycle).stimulus_id
            if stimuli and stimuli != {expected}:
                yield f"cycle {cycle} used {stimuli}, not the deck's {expected}"

    def arms_share_completed_cycles() -> Iterator[str]:
        for cycle in completed:
            arms = {s.arm_id for s in repository.list_cycle_snapshots(run_id, cycle=cycle)}
            if arms != set(configuration.arms):
                missing = sorted(a.value for a in set(configuration.arms) - arms)
                yield f"cycle {cycle} is missing {', '.join(missing)}"

    def within_budget() -> Iterator[str]:
        budget = configuration.memory_budget_tokens
        for arm, snapshots in snapshots_by_arm.items():
            for snapshot in snapshots:
                if snapshot.tokens_after > budget:
                    yield (
                        f"{arm.value} cycle {snapshot.cycle} ends at "
                        f"{snapshot.tokens_after}/{budget}"
                    )

    def pinned_memory_survives() -> Iterator[str]:
        pinned = configuration.pinned_origin_memory_id
        from attention_sink.domain import ArmId

        for snapshot in snapshots_by_arm.get(ArmId.ARM_SINK, ()):
            if pinned not in snapshot.active_memory_ids_after:
                yield f"the pinned memory left arm_sink at cycle {snapshot.cycle}"
                return

    def random_provenance_replays() -> Iterator[str]:
        if not configuration.random_seed:
            yield "the run records no random seed; its stochastic arm is not replayable"

    def dreamer_lineage_resolves() -> Iterator[str]:
        for arm, snapshots in snapshots_by_arm.items():
            known = {s.candidate_memory_id for s in snapshots}
            known |= set(snapshots[0].active_memory_ids_before) if snapshots else set()
            for snapshot in snapshots:
                summary = snapshot.created_summary
                if summary is None:
                    continue
                if len(summary.parent_memory_ids) < 2:
                    yield f"{arm.value} cycle {snapshot.cycle} summary has fewer than two parents"
                unknown = set(summary.parent_memory_ids) - known
                if unknown:
                    yield f"{arm.value} summary names unknown parents {sorted(unknown)}"
                known.add(summary.memory_id)

    def snapshots_unchanged() -> Iterator[str]:
        for arm, snapshots in snapshots_by_arm.items():
            for snapshot in snapshots:
                if not snapshot.verify_hash():
                    yield f"{arm.value} cycle {snapshot.cycle} snapshot hash does not match"

    def nothing_forgotten_returned() -> Iterator[str]:
        for arm, snapshots in snapshots_by_arm.items():
            gone: set[str] = set()
            for snapshot in snapshots:
                returned = gone & set(snapshot.active_memory_ids_after)
                if returned:
                    yield f"{arm.value} cycle {snapshot.cycle} re-activated {sorted(returned)}"
                gone |= {r.memory_id for r in snapshot.retired_memories}

    def interviews_are_read_only() -> Iterator[str]:
        """An interview may never add a memory to the arm it interviewed.

        Asked of provenance, not of text. A faithful answer quotes the memory it is
        answering from, so comparing strings would flag an interview for doing its
        job -- and it did, the first time this ran against real models. Every memory
        an arm holds has to be traceable to something a *cycle* produced: a seed it
        started with, a candidate a writer wrote, or a summary the Dreamer made.
        """
        for arm, snapshots in snapshots_by_arm.items():
            if not snapshots:
                continue
            accounted = set(snapshots[0].active_memory_ids_before)
            accounted |= {s.candidate_memory_id for s in snapshots}
            accounted |= {
                s.created_summary.memory_id for s in snapshots if s.created_summary is not None
            }
            state = states.get(arm.value)
            if state is None:
                continue
            unaccounted = {m.memory_id for m in state.memories} - accounted
            if unaccounted:
                yield (
                    f"{arm.value} holds {len(unaccounted)} memories no cycle produced: "
                    f"{sorted(unaccounted)[:3]}"
                )

    def graveyard_distinguishes_compression() -> Iterator[str]:
        for arm, snapshots in snapshots_by_arm.items():
            for entry in build_graveyard(run_id, snapshots):
                compressed = entry.status is MemoryStatus.COMPRESSED
                if compressed and entry.summary_descendant_id is None:
                    yield f"{arm.value} {entry.memory_id} is compressed with no summary"
                if compressed and entry.genuinely_inaccessible:
                    yield f"{arm.value} {entry.memory_id} is compressed but marked inaccessible"

    def metric_evidence_resolves() -> Iterator[str]:
        for metric in repository.get_metrics(run_id):
            state = states.get(metric.arm_id.value)
            if state is None:
                continue
            unknown = [m for m in metric.cited_memory_ids if state.get(m) is None]
            if unknown:
                yield f"{metric.metric_name} cites unknown memories {unknown[:3]}"

    def seeds_are_present() -> Iterator[str]:
        for arm, state in states.items():
            seeds = [m for m in state.memories if m.memory_kind is MemoryKind.SEED]
            expected = len(bundle.seed_world.memories)
            if len(seeds) != expected:
                yield f"{arm} holds {len(seeds)} seed memories, not {expected}"

    def no_future_stimuli_through_the_api() -> Iterator[str]:
        from fastapi.testclient import TestClient

        from attention_sink.api import build_app

        with TestClient(build_app(repository)) as client:
            ahead = run.current_cycle + 1
            if client.get(f"/runs/{run_id}/cycles/{ahead}").status_code != 404:
                yield f"the API served cycle {ahead}, which has not been committed"
            body = client.get(f"/runs/{run_id}/cycles").json()
            served = set(body["data"]["items"])
            if served - set(completed):
                yield f"the API listed uncommitted cycles {sorted(served - set(completed))}"

    def no_duplicate_snapshot() -> Iterator[str]:
        """One snapshot per arm per cycle, and never two."""
        for arm, snapshots in snapshots_by_arm.items():
            seen: set[int] = set()
            for snapshot in snapshots:
                if snapshot.cycle in seen:
                    yield f"{arm.value} has more than one snapshot for cycle {snapshot.cycle}"
                seen.add(snapshot.cycle)

    def checkpoint_interviews_exist() -> Iterator[str]:
        """Six interviews at every checkpoint the run has reached."""
        interviews = repository.get_interviews(run_id)
        by_cycle: dict[int, set[str]] = {}
        for interview in interviews:
            by_cycle.setdefault(interview.cycle, set()).add(interview.arm_id.value)
        for checkpoint in configuration.checkpoint_cycles:
            if checkpoint > run.current_cycle:
                continue
            arms = by_cycle.get(checkpoint, set())
            if len(arms) != len(configuration.arms):
                yield f"checkpoint {checkpoint} has {len(arms)} interviews, not six"

    def analysis_is_complete() -> Iterator[str]:
        """Analysis has covered every committed cycle, and produced all four metrics.

        Asked of the analysis status rather than of the metric rows. Which cycles
        carry a metric row is a property of the metric -- a Graveyard Echo exists at
        the cycle a memory was retired, not at every cycle -- while the status is the
        analysis's own record of what it looked at.
        """
        status = repository.get_analysis_status(run_id, analysis_name="all")
        if status is None:
            yield "the run has no analysis status; nothing has been scored"
            return
        uncovered = sorted(set(completed) - set(status.completed_cycles))
        if uncovered:
            yield f"analysis has not covered cycles {uncovered[:5]}"
        names = {metric.metric_name for metric in repository.get_metrics(run_id)}
        for required in ("origin_recall", "identity_drift", "contradiction_rate"):
            if required not in names:
                yield f"the run has no {required} metric"
        # An echo is a new memory measured against what its arm could no longer see,
        # so it needs a retirement in a *strictly earlier* cycle than some snapshot.
        # Before that the absence of the metric is the correct answer, not a failure:
        # nothing has been forgotten long enough for anything to echo it.
        echoable = any(
            snapshot.cycle > retirement
            for snapshots in snapshots_by_arm.values()
            for retirement in {s.cycle for s in snapshots if s.retired_memories}
            for snapshot in snapshots
        )
        if echoable and not any(name.startswith("graveyard_echo") for name in names):
            yield (
                f"a memory was retired before a later cycle but no echo was "
                f"measured: {sorted(names)}"
            )

    def checkpoint_analysis_exists() -> Iterator[str]:
        """Divergence, contradiction, and both identity metrics at every checkpoint."""
        reached = [c for c in configuration.checkpoint_cycles if c <= run.current_cycle]
        metrics = repository.get_metrics(run_id)
        for checkpoint in reached:
            at = {m.metric_name for m in metrics if m.cycle == checkpoint}
            for required in ("origin_recall", "identity_drift", "contradiction_rate"):
                if required not in at:
                    yield f"checkpoint {checkpoint} has no {required}"
        divergence = repository.get_analysis_artifact(run_id, name="divergence") or {}
        matrices = divergence.get("matrices", {})
        for checkpoint in reached:
            if str(checkpoint) not in matrices:
                yield f"checkpoint {checkpoint} has no pairwise divergence matrix"
        for name in ("echoes", "contradictions", "question_scores"):
            if repository.get_analysis_artifact(run_id, name=name) is None:
                yield f"the run has no {name} artefact"

    def model_usage_stays_within_its_limits() -> Iterator[str]:
        """What the run spent, against the ceilings the protocol declared."""
        limits = configuration.model_call_limits
        usage = run.usage
        if usage.total_calls > limits.max_model_calls_per_run:
            yield f"{usage.total_calls} calls against a ceiling of {limits.max_model_calls_per_run}"
        cycles = len(completed)
        expectations = {
            "writer": limits.writer_calls_per_cycle * cycles,
            "token_counter": limits.token_count_calls_per_cycle * cycles,
            "summarizer": limits.summary_calls_per_cycle * cycles,
        }
        for role, ceiling in expectations.items():
            spent = usage.calls_by_role.get(role, 0)
            if spent > ceiling:
                yield f"{spent} {role} calls over {cycles} cycles, above the {ceiling} allowed"

    def graveyard_covers_every_eviction() -> Iterator[str]:
        """Nothing an arm retired is missing from the record of what it lost."""
        for arm, snapshots in snapshots_by_arm.items():
            retired = {r.memory_id for s in snapshots for r in s.retired_memories}
            recorded = {entry.memory_id for entry in build_graveyard(run_id, snapshots)}
            missing = retired - recorded
            if missing:
                yield f"{arm.value} retired {len(missing)} memories the graveyard does not hold"

    def compression_records_are_distinct() -> Iterator[str]:
        """A summary is one memory, written once, from parents named once."""
        for arm, snapshots in snapshots_by_arm.items():
            summaries = [s.created_summary for s in snapshots if s.created_summary is not None]
            ids = [summary.memory_id for summary in summaries]
            if len(ids) != len(set(ids)):
                yield f"{arm.value} wrote the same summary identifier twice"
            for summary in summaries:
                if len(set(summary.parent_memory_ids)) != len(summary.parent_memory_ids):
                    yield f"{arm.value} summary {summary.memory_id} names a parent twice"

    def export_checksums_pass() -> Iterator[str]:
        if export is None:
            return
        if not (export / "checksums.sha256").is_file():
            yield f"no checksums.sha256 in {export}"
            return
        missing = [name for name in EXPORT_FILES if not (export / name).is_file()]
        if missing:
            yield f"the export is missing {', '.join(missing)}"
        failures = verify_checksums(export)
        if failures:
            yield f"checksum mismatch: {', '.join(failures)}"

    def export_record_counts_match_the_run() -> Iterator[str]:
        """The dataset holds exactly what the store does, not a subset of it."""
        if export is None:
            return
        expected = {
            "cycle-snapshots.jsonl": sum(len(s) for s in snapshots_by_arm.values()),
            "interviews.jsonl": len(repository.get_interviews(run_id)),
            "metrics.jsonl": len(repository.get_metrics(run_id)),
        }
        for name, count in expected.items():
            path = export / name
            if not path.is_file():
                continue
            written = sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line)
            if written != count:
                yield f"{name} holds {written} records; the store holds {count}"

    def export_carries_no_secret() -> Iterator[str]:
        """Nothing that looks like a credential, a token, or a scoring key."""
        if export is None:
            return
        for name in (*EXPORT_FILES, "checksums.sha256"):
            path = export / name
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for marker in SECRET_MARKERS:
                if marker in text:
                    yield f"{name} contains {marker!r}"

    def export_holds_no_unreleased_data() -> Iterator[str]:
        """No snapshot beyond the last committed cycle reached the dataset."""
        if export is None:
            return
        path = export / "cycle-snapshots.jsonl"
        if not path.is_file():
            return
        highest = max(
            (
                int(json.loads(line)["cycle"])
                for line in path.read_text(encoding="utf-8").splitlines()
                if line
            ),
            default=0,
        )
        if highest > run.current_cycle:
            yield f"the export holds cycle {highest}; the run has committed {run.current_cycle}"

    checks: tuple[tuple[str, Check], ...] = (
        ("six arms exist", six_arms),
        ("seed states match", seed_states_match),
        ("one stimulus per cycle", one_stimulus_per_cycle),
        ("arms share every completed cycle", arms_share_completed_cycles),
        ("no budget violations", within_budget),
        ("pinned memory survives", pinned_memory_survives),
        ("random provenance replays", random_provenance_replays),
        ("dreamer lineage resolves", dreamer_lineage_resolves),
        ("no completed snapshot changed", snapshots_unchanged),
        ("no forgotten memory returned", nothing_forgotten_returned),
        ("interviews are read-only", interviews_are_read_only),
        ("graveyard distinguishes compression", graveyard_distinguishes_compression),
        ("metric evidence resolves", metric_evidence_resolves),
        ("every seed is accounted for", seeds_are_present),
        ("no future stimuli through the API", no_future_stimuli_through_the_api),
        ("no duplicate snapshot", no_duplicate_snapshot),
        ("checkpoint interviews exist", checkpoint_interviews_exist),
        ("analysis is complete", analysis_is_complete),
        ("checkpoint analysis exists", checkpoint_analysis_exists),
        ("model usage stays within its limits", model_usage_stays_within_its_limits),
        ("graveyard covers every eviction", graveyard_covers_every_eviction),
        ("compression records are distinct", compression_records_are_distinct),
        ("export checksums pass", export_checksums_pass),
        ("export record counts match the run", export_record_counts_match_the_run),
        ("export carries no secret", export_carries_no_secret),
        ("export holds no unreleased data", export_holds_no_unreleased_data),
    )

    print(f"verifying {run_id} at cycle {run.current_cycle}/{configuration.maximum_cycles}")
    failed = 0
    for name, check in checks:
        problems = list(check())
        if problems:
            failed += 1
            print(f"  FAIL  {name}")
            for problem in problems[:5]:
                print(f"          {problem}")
            if len(problems) > 5:
                print(f"          ... and {len(problems) - 5} more")
        else:
            print(f"  ok    {name}")
    if failed:
        print(f"FAILED: {failed} of {len(checks)} checks", file=sys.stderr)
        return 1
    print(f"all {len(checks)} checks passed")
    return 0


def main() -> int:
    """Parse arguments and verify one run."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--source", choices=("local", "aws"), default="local")
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--export", type=Path, default=None)
    args = parser.parse_args()
    return run_checks(
        repository=open_repository(args.source, args.database),
        run_id=args.run_id,
        root=args.root,
        export=args.export,
    )


if __name__ == "__main__":
    sys.exit(main())
