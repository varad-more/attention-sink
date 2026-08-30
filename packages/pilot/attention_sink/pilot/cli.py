"""The local pilot commands: validate, calibrate, freeze, run, export.

Five verbs in the order a protocol goes through them. `validate` says whether the
files agree with each other. `calibrate` fills in every derived field, the
active-memory budget included, using the counter the run will actually be measured
by. `freeze` seals the result. `run` executes it locally and, when asked, writes an
export directory.

Nothing here holds logic of its own. Each command is a thin wrapper over
`protocol.py`, `engine.py`, and `export.py`, so that anything worth testing is
testable without a subprocess.
"""

from __future__ import annotations

import argparse
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from attention_sink.model_gateway import (
    FIXTURE_MODEL_ID,
    FIXTURE_REGION,
    ExactTokenCounter,
    GatewaySettings,
    ModelGateway,
    build_gateway,
)
from attention_sink.pilot.configuration import ModelSpec, PilotRunConfiguration
from attention_sink.pilot.engine import CheckpointRecord, PilotEngine
from attention_sink.pilot.export import export_run
from attention_sink.pilot.protocol import (
    DEFAULT_PROTOCOL_ROOT,
    ProtocolBundle,
    ProtocolError,
    freeze_documents,
    load_bundle,
    rewrite_scalars,
)
from attention_sink.pilot.snapshots import ArmCycleSnapshot
from attention_sink.protocol import current_version

__all__ = [
    "BUDGET_HEADROOM_RATIO",
    "BUDGET_ROUNDING",
    "build_run",
    "calibrate",
    "main",
    "model_specs",
    "proposed_budget",
]

BUDGET_HEADROOM_RATIO = 1.5
"""How much larger the active-memory budget is than the seed set that starts in it.

Chosen so the budget binds partway through the orientation phase rather than at
cycle 1 or not at all. At the pilot's generated-memory size that is roughly five
cycles of headroom: long enough that every arm gets to establish itself on identical
state, short enough that eighteen of the twenty-four cycles are spent under pressure.
Too tight and every arm is a stateless arm; too loose and the mechanism never runs.
"""

BUDGET_ROUNDING = 8
"""The budget is rounded up to a multiple of this. A round number is easier to
reason about in a manifest than a derived one, and the rounding is upward so it can
only ever loosen the ceiling, never tighten it below what was computed."""

DEFAULT_RUN_DIRECTORY = Path(".pilot-runs/local")


# ------------------------------------------------------------------ calibration


def proposed_budget(seed_tokens: int) -> int:
    """The active-memory budget a seed set of ``seed_tokens`` implies."""
    target = seed_tokens * BUDGET_HEADROOM_RATIO
    return int(math.ceil(target / BUDGET_ROUNDING) * BUDGET_ROUNDING)


def _rewrite_nested(path: Path, *, anchor: str, values: Mapping[str, Mapping[str, str]]) -> None:
    """Replace fields inside each list item of a YAML file, in place.

    Walks the file line by line, tracking which item it is inside from the ``anchor``
    key that opens each one, and substitutes only the named fields. A parse-and-dump
    round trip would be shorter and would also discard every comment in the file and
    reflow every folded block; the protocol files are meant to be read.

    Raises:
        ProtocolError: An expected field was not found inside an item.
    """
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    pending: dict[str, dict[str, str]] = {k: dict(v) for k, v in values.items()}
    current: str | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(f"- {anchor}:"):
            current = stripped.split(":", 1)[1].strip()
            continue
        if current is None or current not in pending:
            continue
        key = stripped.split(":", 1)[0]
        if key in pending[current] and stripped.startswith(f"{key}:"):
            indent = line[: len(line) - len(line.lstrip())]
            lines[index] = f"{indent}{key}: {pending[current].pop(key)}\n"
    unwritten = {item: sorted(fields) for item, fields in pending.items() if fields}
    if unwritten:
        msg = f"{path} has no field to write for: {unwritten}"
        raise ProtocolError(msg)
    path.write_text("".join(lines), encoding="utf-8")


def calibrate(bundle: ProtocolBundle, counter: ExactTokenCounter) -> tuple[int, int]:
    """Fill in every derived protocol field, and fix the budget.

    Counts each seed memory with the counter the run will be measured by, writes those
    counts and the content digests into the seed world and the stimulus deck, and
    writes the resulting budget and counter version into the protocol.

    Calibration is separate from freezing on purpose. The budget cannot be known until
    the seed world has been measured, and a budget frozen before it was measured would
    be a number nobody derived from anything.

    Returns:
        The seed set's total token cost, and the budget that was written.
    """
    root = bundle.root
    seeds = bundle.seed_world.memories
    counts = {seed.memory_id: counter.count(seed.text) for seed in seeds}
    blank = sorted(memory_id for memory_id, count in counts.items() if count < 1)
    if blank:
        msg = f"the counter reports no tokens for seeds {blank}; they cannot be budgeted"
        raise ProtocolError(msg)

    _rewrite_nested(
        root / bundle.paths[1],
        anchor="memory_id",
        values={
            seed.memory_id: {
                "token_count": str(counts[seed.memory_id]),
                "content_hash": f"'{seed.expected_content_hash}'",
            }
            for seed in seeds
        },
    )
    _rewrite_nested(
        root / bundle.paths[2],
        anchor="stimulus_id",
        values={
            stimulus.stimulus_id: {"content_hash": f"'{stimulus.expected_content_hash}'"}
            for stimulus in bundle.stimulus_deck.stimuli
        },
    )

    total = sum(counts.values())
    budget = proposed_budget(total)
    rewrite_scalars(root / bundle.paths[1], {"counter_version": counter.version})
    rewrite_scalars(
        root / bundle.paths[0],
        {"memory_budget_tokens": str(budget), "counter_version": counter.version},
    )
    return total, budget


# --------------------------------------------------------------------- wiring


def model_specs(gateway: ModelGateway) -> tuple[ModelSpec, ModelSpec]:
    """The writer and embedding roles as the built gateway actually resolved them.

    Read off the gateway rather than off configuration, so the manifest records the
    models that were used and not the ones that were requested.
    """
    settings = gateway.settings
    inference = settings.inference
    models = settings.models
    region = FIXTURE_REGION if models is None else models.region
    writer_id = FIXTURE_MODEL_ID if models is None else models.writer_model_id
    embedding_id = FIXTURE_MODEL_ID if models is None else models.embedding_model_id

    def spec(model_id: str, max_output_tokens: int) -> ModelSpec:
        return ModelSpec(
            model_id=model_id,
            region=region,
            temperature=inference.temperature,
            top_p=inference.top_p,
            max_output_tokens=max_output_tokens,
            simulated=gateway.simulated,
        )

    return spec(writer_id, inference.writer_max_tokens), spec(
        embedding_id, inference.summary_max_tokens
    )


def build_run(
    bundle: ProtocolBundle,
    *,
    run_id: str,
    gateway: ModelGateway | None = None,
    canonical: bool = False,
    now: datetime | None = None,
) -> PilotEngine:
    """Build an initialised engine for ``bundle``, ready to run cycle 1.

    Raises:
        ProtocolError: The bundle is not frozen, has drifted, or is uncalibrated.
    """
    bundle.require_runnable()
    resolved = gateway if gateway is not None else build_gateway(GatewaySettings.from_env())
    writer, embedding = model_specs(resolved)
    version = current_version()
    configuration = PilotRunConfiguration.from_bundle(
        bundle,
        run_id=run_id,
        created_at=now or datetime.now(UTC),
        writer_model=writer,
        embedding_model=embedding,
        prompt_set_digest=resolved.prompts.prompt_set_digest(bundle.protocol.writer_prompt_version),
        app_version=version.app_version,
        git_commit=version.git_commit,
        canonical=canonical,
    )
    engine = PilotEngine(configuration=configuration, bundle=bundle, gateway=resolved)
    engine.initialize_pilot_run()
    return engine


def run_cycles(
    engine: PilotEngine, cycles: int
) -> tuple[list[ArmCycleSnapshot], list[CheckpointRecord]]:
    """Run ``cycles`` cycles, interviewing at every checkpoint the run passes.

    The checkpoint at cycle 0 runs before the first cycle, so an arm's answers at the
    end are comparable with its answers before it had written anything.
    """
    snapshots: list[ArmCycleSnapshot] = []
    checkpoints: list[CheckpointRecord] = []
    if engine.configuration.is_checkpoint(engine.current_cycle):
        checkpoints.extend(engine.run_checkpoint(engine.current_cycle))
    for _ in range(cycles):
        cycle = engine.current_cycle + 1
        snapshots.extend(engine.run_cycle(cycle))
        if engine.configuration.is_checkpoint(cycle):
            checkpoints.extend(engine.run_checkpoint(cycle))
    return snapshots, checkpoints


# ------------------------------------------------------------------- commands


def _load(args: argparse.Namespace) -> ProtocolBundle:
    return load_bundle(args.root, protocol_version=args.protocol_version)


def _command_validate(args: argparse.Namespace) -> int:
    bundle = _load(args)
    print(f"protocol {bundle.protocol.protocol_version} at {bundle.root}")
    for name, document in bundle.named_documents:
        recomputed = bundle.digests[name]
        state = document.status.value
        mark = "ok"
        if document.is_frozen and document.content_hash != recomputed:
            mark = "MODIFIED SINCE FREEZING"
        print(f"  {name:<48} {state:<8} {mark}")
        if mark != "ok":
            print(f"      recorded:   {document.content_hash or '(none)'}")
            print(f"      recomputed: {recomputed}")
    calibrated = bundle.protocol.is_calibrated and bundle.seed_world.is_calibrated
    print(f"  seeds: {len(bundle.seed_world.memories)}", end="")
    print(f"  stimuli: {len(bundle.stimulus_deck.stimuli)}", end="")
    print(f"  facts: {len(bundle.truth_ledger.facts)}", end="")
    print(f"  questions: {len(bundle.interview.questions)}")
    print(f"  calibrated: {calibrated}  frozen: {bundle.is_frozen}")
    drifted = bundle.drifted()
    if drifted:
        print(f"FAILED: modified after freezing: {', '.join(drifted)}", file=sys.stderr)
        return 1
    return 0


def _command_calibrate(args: argparse.Namespace) -> int:
    bundle = _load(args)
    gateway = build_gateway(GatewaySettings.from_env())
    total, budget = calibrate(bundle, gateway.token_counter)
    print(f"counter:      {gateway.token_counter.version}")
    print(f"seed tokens:  {total}")
    print(f"budget:       {budget}  ({BUDGET_HEADROOM_RATIO}x, rounded to {BUDGET_ROUNDING})")
    print("re-validating")
    return _command_validate(args)


def _command_freeze(args: argparse.Namespace) -> int:
    bundle = _load(args)
    written = freeze_documents(bundle)
    if not written:
        print("already frozen; nothing to write")
    for name in written:
        print(f"froze {name}")
    return _command_validate(args)


def _command_run(args: argparse.Namespace) -> int:
    bundle = _load(args)
    engine = build_run(bundle, run_id=args.run_id, canonical=args.canonical)
    configuration = engine.configuration
    if configuration.simulated:
        print("SIMULATED: no model produced anything in this run.")
    print(f"run {configuration.run_id}: {len(configuration.arms)} arms, ", end="")
    print(f"{configuration.memory_budget_tokens} token budget, ", end="")
    print(f"{configuration.max_cycles} cycles")

    snapshots, checkpoints = run_cycles(engine, args.cycles)
    for arm_id in configuration.arms:
        state = engine.state_of(arm_id)
        print(
            f"  {arm_id.value:<14} active={len(state.active_memory_ids):>3} "
            f"tokens={state.active_tokens:>4}/{configuration.memory_budget_tokens} "
            f"retired={len(state.memories) - len(state.active_memories):>3}"
        )
    usage = engine.budget.usage
    print(f"  model calls: {usage.total_calls} {usage.calls_by_role}")

    if args.out is not None:
        result = export_run(
            args.out,
            run=engine.run_snapshot(),
            snapshots=snapshots,
            checkpoints=checkpoints,
            bundle=bundle,
        )
        print(f"exported {len(result.files)} files to {result.directory}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pilot", description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument("--protocol-version", default="pilot-v1")
    subcommands = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("validate", _command_validate),
        ("calibrate", _command_calibrate),
        ("freeze", _command_freeze),
    ):
        subcommands.add_parser(name).set_defaults(handler=handler)

    run = subcommands.add_parser("run")
    run.add_argument("--cycles", type=int, default=24)
    run.add_argument("--run-id", default="pilot_local")
    run.add_argument("--out", type=Path, default=None)
    run.add_argument("--canonical", action="store_true")
    run.set_defaults(handler=_command_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one pilot command.

    Returns:
        A process exit status. Protocol failures return 1 with a message on stderr
        rather than a traceback: a draft protocol is an ordinary state, not a crash.
    """
    args = _parser().parse_args(argv)
    try:
        exit_status: int = args.handler(args)
    except ProtocolError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return exit_status


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
