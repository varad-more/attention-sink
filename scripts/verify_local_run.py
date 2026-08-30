#!/usr/bin/env python
"""Check a persisted local run against every invariant it claims to satisfy.

Reads the database and the export; writes nothing. Exits non-zero on the first
failing check so it is usable as a gate, and prints every check either way so a
passing run is as legible as a failing one.

The checks are the scientific invariants restated as questions about stored data. A
run that passes them is not necessarily interesting; a run that fails one is not
evidence about anything.

    python scripts/verify_local_run.py --run-id run_local_pilot [--export DIR]
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Iterator
from pathlib import Path

from attention_sink.analysis import build_graveyard, verify_checksums
from attention_sink.domain import MemoryKind, MemoryStatus
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot.local import DEFAULT_DATABASE, DEFAULT_RUN_ID
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT, load_bundle

Check = Callable[[], Iterator[str]]
"""A check yields one line per problem, and nothing at all when it passes."""


def run_checks(*, database: Path, run_id: str, root: Path, export: Path | None) -> int:
    """Run every check and report. Returns a process exit status."""
    repository = SqliteRepository(database)
    bundle = load_bundle(root)
    run = repository.get_run(run_id)
    if run is None:
        print(f"FAILED: no run {run_id} in {database}", file=sys.stderr)
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
        answers = {
            str(entry["answer"])
            for interview in repository.get_interviews(run_id)
            for entry in interview.answers
        }
        for arm, state in states.items():
            held = {memory.text for memory in state.memories}
            if held & answers:
                yield f"{arm} holds a memory whose text is an interview answer"

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

    def export_checksums_pass() -> Iterator[str]:
        if export is None:
            return
        if not (export / "checksums.sha256").is_file():
            yield f"no checksums.sha256 in {export}"
            return
        failures = verify_checksums(export)
        if failures:
            yield f"checksum mismatch: {', '.join(failures)}"

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
        ("export checksums pass", export_checksums_pass),
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
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--export", type=Path, default=None)
    args = parser.parse_args()
    return run_checks(
        database=args.database, run_id=args.run_id, root=args.root, export=args.export
    )


if __name__ == "__main__":
    sys.exit(main())
