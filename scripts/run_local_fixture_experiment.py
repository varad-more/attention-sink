#!/usr/bin/env python
"""Run the whole twenty-four cycle pilot locally, and export every snapshot.

Six identical arms, interviews at cycles 0, 12, and 24, one shared stimulus per
cycle, and a complete export directory at the end. Nothing here reaches a network.

Everything it produces is SIMULATED, LOCAL, and NON-CANONICAL. Fixture generations
validate that the application sequences a cycle correctly. They say nothing about how
a real model would remember.

    python scripts/run_local_fixture_experiment.py [--out .pilot-runs/local]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from attention_sink.pilot import build_run, export_run, load_bundle, run_cycles
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT

DEFAULT_OUT = Path(".pilot-runs/local")


def main() -> int:
    """Run the full local experiment and export it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--run-id", default="pilot_local")
    parser.add_argument("--cycles", type=int, default=None)
    args = parser.parse_args()

    bundle = load_bundle(args.root)
    engine = build_run(bundle, run_id=args.run_id)
    configuration = engine.configuration
    cycles = configuration.maximum_cycles if args.cycles is None else args.cycles

    print("SIMULATED - LOCAL - NON-CANONICAL")
    print("Fixture generations. Not evidence about any model.")
    print(
        f"run {configuration.run_id} [{configuration.run_kind.value}]: "
        f"{len(configuration.arms)} arms, {cycles} cycles, "
        f"{configuration.memory_budget_tokens} tokens "
        f"({configuration.token_count_source})\n"
    )

    snapshots, checkpoints = run_cycles(engine, cycles)
    by_cycle: dict[int, list[str]] = {}
    for snapshot in snapshots:
        by_cycle.setdefault(snapshot.cycle, []).append(
            f"{snapshot.tokens_after:>3}/{len(snapshot.active_memory_ids_after):<2}"
        )
    header = "  ".join(f"{arm.value.removeprefix('arm_'):>6}" for arm in configuration.arms)
    print(f"  cycle  {header}      (tokens/active)")
    for cycle in sorted(by_cycle):
        print(f"  {cycle:>5}  " + "  ".join(f"{cell:>6}" for cell in by_cycle[cycle]))

    print(f"\n  checkpoints: {sorted({record.cycle for record in checkpoints})}")
    usage = engine.budget.usage
    print(f"  model calls: {usage.total_calls} {usage.calls_by_role}")

    result = export_run(
        args.out,
        run=engine.run_snapshot(),
        snapshots=snapshots,
        checkpoints=checkpoints,
        bundle=bundle,
    )
    print(
        f"  exported {len(result.files)} files to {result.directory} (simulated={result.simulated})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
