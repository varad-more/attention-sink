#!/usr/bin/env python
"""A local stand-in for EventBridge: one cycle per tick, never two at once.

Simulates the production scheduler closely enough to find the bugs that a scheduler
finds. It fires on an interval, advances the run by exactly one cycle, and stops at
the configured end. It calls the same application service the manual command calls, so
a bug reachable from a tick is reachable from a keystroke and vice versa.

What it will not do:

- run two cycles at once, because the cycle lock refuses the second
- advance a paused run
- advance a completed run
- advance past the next expected cycle, because the commit checks the run version

    python scripts/run_local_scheduler.py --interval 2 --run-id run_local_pilot
    python scripts/run_local_scheduler.py --once
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

from attention_sink.model_gateway import GatewaySettings, build_gateway
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot.local import DEFAULT_DATABASE, DEFAULT_RUN_ID
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT, load_bundle
from attention_sink.pilot.repositories import LockNotHeld
from attention_sink.pilot.service import PilotService, RunPaused, ServiceError


def log(event: str, **fields: object) -> None:
    """One structured line per scheduler event.

    Deliberately not the logging module. A local scheduler's output is read by a
    person watching a terminal, and one line per tick with its fields in it is what
    that person can actually follow.
    """
    stamped = datetime.now(UTC).isoformat(timespec="seconds")
    details = "  ".join(f"{key}={value}" for key, value in fields.items())
    print(f"{stamped}  {event:<18} {details}".rstrip())


def tick(service: PilotService, run_id: str) -> bool:
    """Advance the run by one cycle. Returns False when there is nothing left to do."""
    invocation = uuid.uuid4().hex[:12]
    try:
        run = service.get_run(run_id)
    except ServiceError as exc:
        log("no-such-run", run_id=run_id, error=exc)
        return False
    if run.paused:
        log("paused", run_id=run_id, cycle=run.current_cycle)
        return False
    if run.is_complete:
        log("complete", run_id=run_id, cycle=run.current_cycle)
        return False

    try:
        outcome = service.run_next_cycle(run_id, invocation_id=invocation)
    except LockNotHeld as exc:
        log("lock-held", run_id=run_id, invocation=invocation, detail=exc)
        return True
    except RunPaused:
        log("paused", run_id=run_id)
        return False
    except ServiceError as exc:
        log("refused", run_id=run_id, error=exc)
        return False

    log(
        "cycle-committed",
        run_id=run_id,
        cycle=outcome.cycle,
        arms=len(outcome.snapshots),
        interviews=len(outcome.checkpoints),
        reused=outcome.reused_prepared_cycle,
        invocation=invocation,
    )
    return not outcome.run.is_complete


def main() -> int:
    """Run the scheduler until the run completes or the interval count runs out."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    parser.add_argument("--interval", type=float, default=1.0, help="seconds between ticks")
    parser.add_argument("--max-ticks", type=int, default=0, help="0 means until complete")
    parser.add_argument("--once", action="store_true", help="fire exactly one tick")
    args = parser.parse_args()

    repository = SqliteRepository(args.database)
    service = PilotService(
        repository=repository,
        bundle=load_bundle(args.root),
        gateway=build_gateway(GatewaySettings.from_env()),
    )
    log("scheduler-start", run_id=args.run_id, interval=args.interval, once=args.once)

    ticks = 0
    while True:
        if not tick(service, args.run_id):
            break
        ticks += 1
        if args.once or (args.max_ticks and ticks >= args.max_ticks):
            break
        time.sleep(args.interval)

    log("scheduler-stop", run_id=args.run_id, ticks=ticks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
