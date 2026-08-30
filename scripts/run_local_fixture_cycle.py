#!/usr/bin/env python
"""Run one local fixture cycle and print what each of the six arms did with it.

The smallest thing that exercises the whole cycle sequence: one stimulus, six
policy-blind writer requests, citation validation, rebalancing, staging, the
cross-arm check, and the commit. Useful when a change should not have altered
behaviour, because the output is short enough to read.

Everything it produces is SIMULATED, LOCAL, and NON-CANONICAL.

    python scripts/run_local_fixture_cycle.py [--cycle 1] [--root experiment/pilot]
"""

from __future__ import annotations

import argparse
from pathlib import Path

from attention_sink.pilot import PilotEngine, build_run, load_bundle
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT


def advance_to(engine: PilotEngine, cycle: int) -> None:
    """Run every cycle before ``cycle`` so it can be the one that is inspected."""
    while engine.current_cycle < cycle - 1:
        engine.run_cycle(engine.current_cycle + 1)


def main() -> int:
    """Run one cycle and report it."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--cycle", type=int, default=1)
    parser.add_argument("--run-id", default="pilot_local_cycle")
    args = parser.parse_args()

    engine = build_run(load_bundle(args.root), run_id=args.run_id)
    print(f"SIMULATED - LOCAL - NON-CANONICAL ({engine.configuration.run_kind.value})")
    advance_to(engine, args.cycle)

    stimulus = engine.prepare_cycle(args.cycle)
    print(f"\ncycle {stimulus.cycle}  {stimulus.stimulus_id}  [{stimulus.phase}]")
    print(f"  {stimulus.text}\n")

    for snapshot in engine.run_cycle(args.cycle):
        summary = "" if snapshot.created_summary is None else "  +summary"
        print(
            f"  {snapshot.arm_id.value:<14} "
            f"tokens {snapshot.tokens_before:>3} -> {snapshot.tokens_after:>3}"
            f"/{snapshot.budget_tokens}  "
            f"cited {len(snapshot.validated_citations)}/{len(snapshot.claimed_citations)}  "
            f"retired {len(snapshot.retired_memories)}{summary}"
        )
    usage = engine.budget.usage
    print(f"\n  model calls: {usage.total_calls} {usage.calls_by_role}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
