#!/usr/bin/env python
"""The local commands: one database, one run, one cycle at a time.

The composition root. This is the one module that chooses which repository satisfies
the pilot's port, which is why it lives beside the other runners rather than inside a
package: an application that imported its own adapter would have no adapter line left
to move in Phase 7.

Every administrative action is here rather than behind an HTTP route, because an
endpoint that could advance the experiment is an endpoint that could advance it twice.

    python scripts/local_cli.py create
    python scripts/local_cli.py cycle --count 24
    python scripts/local_cli.py analyze | export | status | reset | migrate
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from attention_sink.model_gateway import GatewaySettings, ModelGateway, build_gateway
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot.configuration import RunKind
from attention_sink.pilot.local import (
    DEFAULT_DATABASE,
    DEFAULT_EXPORT,
    DEFAULT_RUN_ID,
    SIMULATED_BANNER,
    build_configuration,
)
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT, ProtocolBundle, load_bundle
from attention_sink.pilot.repositories import PersistenceError
from attention_sink.pilot.service import PilotService

__all__ = ["main", "open_repository"]


def open_repository(path: Path) -> SqliteRepository:
    """Open the local SQLite store, migrating it forward."""
    return SqliteRepository(path)


def _services(args: argparse.Namespace) -> tuple[Any, PilotService, ProtocolBundle, ModelGateway]:
    bundle = load_bundle(args.root)
    gateway = build_gateway(GatewaySettings.from_env())
    repository = open_repository(args.database)
    service = PilotService(repository=repository, bundle=bundle, gateway=gateway)
    return repository, service, bundle, gateway


# ------------------------------------------------------------------- commands


def _command_migrate(args: argparse.Namespace) -> int:
    repository = open_repository(args.database)
    print(f"database: {args.database}")
    print(f"schema version: {repository.schema_version}")
    return 0


def _command_create(args: argparse.Namespace) -> int:
    repository, service, bundle, gateway = _services(args)
    configuration = build_configuration(bundle, run_id=args.run_id, gateway=gateway)
    print(SIMULATED_BANNER)
    try:
        service.create_run(run_id=args.run_id, configuration=configuration)
    except PersistenceError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    checkpoints = service.run_checkpoint(args.run_id, cycle=0)
    print(f"created {args.run_id}: {len(configuration.arms)} arms, ", end="")
    print(f"{configuration.maximum_cycles} cycles, ", end="")
    print(f"{configuration.memory_budget_tokens} tokens ({configuration.token_count_source})")
    print(f"  cycle-0 interviews: {len(checkpoints)}")
    del repository
    return 0


def _command_cycle(args: argparse.Namespace) -> int:
    repository, service, _, _ = _services(args)
    for _ in range(args.count):
        run = service.get_run(args.run_id)
        if run.is_complete:
            print(f"run {args.run_id} is complete at cycle {run.current_cycle}")
            break
        outcome = service.run_next_cycle(args.run_id)
        reused = "  (reused prepared cycle)" if outcome.reused_prepared_cycle else ""
        print(
            f"cycle {outcome.cycle:>2}: {len(outcome.snapshots)} arms committed"
            f"{'' if not outcome.checkpoints else f', {len(outcome.checkpoints)} interviews'}"
            f"{reused}"
        )
    final = service.get_run(args.run_id)
    print(f"run {args.run_id} at cycle {final.current_cycle}/{final.configuration.maximum_cycles}")
    del repository
    return 0


def _command_status(args: argparse.Namespace) -> int:
    repository, service, _, _ = _services(args)
    run = service.get_run(args.run_id)
    print(f"{run.run_id} [{run.run_kind.value}] {run.status.value}")
    print(f"  cycle {run.current_cycle}/{run.configuration.maximum_cycles}  version {run.version}")
    print(f"  model calls: {run.usage.total_calls} {run.usage.calls_by_role}")
    states = repository.get_all_current_arm_states(run.run_id)
    for arm in run.configuration.arms:
        state = states.get(arm.value)
        if state is None:
            continue
        print(
            f"  {arm.value:<14} active={len(state.active_memories):>3} "
            f"tokens={state.active_tokens:>4}/{run.configuration.memory_budget_tokens}"
        )
    print(f"  interviews: {len(repository.get_interviews(run.run_id))}")
    return 0


def _command_analyze(args: argparse.Namespace) -> int:
    from attention_sink.analysis import AnalysisService

    repository, _, bundle, gateway = _services(args)
    analysis = AnalysisService(repository=repository, bundle=bundle, gateway=gateway)
    result = analysis.analyse_run(args.run_id)
    repository.store_analysis_artifact(
        args.run_id, name="divergence", payload={"matrices": result.divergence}
    )
    repository.store_analysis_artifact(
        args.run_id,
        name="echoes",
        payload={"items": [echo.model_dump(mode="json") for echo in result.echoes]},
    )
    repository.store_analysis_artifact(
        args.run_id,
        name="contradictions",
        payload={"items": [f.model_dump(mode="json") for f in result.contradictions]},
    )
    repository.store_analysis_artifact(
        args.run_id,
        name="question_scores",
        payload={"items": [s.model_dump(mode="json") for s in result.question_scores]},
    )
    print(SIMULATED_BANNER)
    print(f"metrics stored:    {len(result.metrics)}")
    print(f"graveyard entries: {len(result.graveyard)}")
    print(f"echo measurements: {len(result.echoes)}")
    print(f"contradictions:    {len(result.contradictions)}")
    print(f"divergence at:     {sorted(result.divergence)}")
    return 0


def _command_export(args: argparse.Namespace) -> int:
    from attention_sink.analysis import AnalysisService, export_dataset

    repository, service, bundle, gateway = _services(args)
    run = service.get_run(args.run_id)
    analysis = AnalysisService(repository=repository, bundle=bundle, gateway=gateway)
    result = export_dataset(
        args.out,
        run=run,
        repository=repository,
        bundle=bundle,
        analysis=analysis.analyse_run(args.run_id),
    )
    print(SIMULATED_BANNER)
    print(f"exported {len(result.files)} files plus checksums to {result.directory}")
    print(f"labels: {', '.join(result.manifest.labels)}")
    return 0


def _command_reset(args: argparse.Namespace) -> int:
    """Delete a run's data, refusing anything that is not a local fixture run."""
    repository, service, _, _ = _services(args)
    try:
        run = service.get_run(args.run_id)
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    if run.run_kind is not RunKind.LOCAL_FIXTURE:
        print(
            f"FAILED: {run.run_id} is {run.run_kind.value}, not local_fixture; "
            f"refusing to delete anything that is not demo data",
            file=sys.stderr,
        )
        return 1
    repository.delete_run(run.run_id)
    print(f"deleted {run.run_id} and everything under it")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="local_cli", description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--run-id", default=DEFAULT_RUN_ID)
    subcommands = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("migrate", _command_migrate),
        ("create", _command_create),
        ("status", _command_status),
        ("analyze", _command_analyze),
        ("reset", _command_reset),
    ):
        subcommands.add_parser(name).set_defaults(handler=handler)

    cycle = subcommands.add_parser("cycle")
    cycle.add_argument("--count", type=int, default=1)
    cycle.set_defaults(handler=_command_cycle)

    export = subcommands.add_parser("export")
    export.add_argument("--out", type=Path, default=DEFAULT_EXPORT)
    export.set_defaults(handler=_command_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one local command.

    Returns:
        A process exit status. Expected refusals return 1 with a message on stderr
        rather than a traceback.
    """
    args = _parser().parse_args(argv)
    status: int = args.handler(args)
    return status


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
