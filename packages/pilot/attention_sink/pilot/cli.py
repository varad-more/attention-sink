"""The local pilot commands: validate, calibrate, local-validate, draft, run.

Five verbs in the order a protocol goes through them. `validate` says whether the
files agree with each other. `calibrate` fills in every derived field, the
active-memory budget included, using the deterministic local counter. `local-validate`
digests the result, promotes it to LOCAL_VALIDATED, and writes the manifest. `draft`
returns it so it can be edited. `run` executes it locally against fixture models and,
when asked, writes an export directory.

Nothing here freezes a protocol. A pilot protocol becomes FROZEN only after AWS token
calibration in Phase 8; freezing a budget denominated in the local counter's tokens
would make the canonical experiment a measurement of the fixture counter.

Nothing here holds logic of its own. Each command is a thin wrapper over
`protocol.py`, `engine.py`, and `export.py`, so that anything worth testing is
testable without a subprocess.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from attention_sink.domain import ArmId, make_memory_id
from attention_sink.model_gateway import (
    BEDROCK_COUNTER_VERSION,
    CONVERSE_COUNTER_VERSION,
    FIXTURE_MODEL_ID,
    FIXTURE_REGION,
    ExactTokenCounter,
    GatewaySettings,
    ModelGateway,
    build_gateway,
)
from attention_sink.pilot.canonical import canonical_digest
from attention_sink.pilot.configuration import ModelSpec, PilotRunConfiguration, RunKind
from attention_sink.pilot.engine import CheckpointRecord, PilotEngine
from attention_sink.pilot.export import export_run
from attention_sink.pilot.protocol import (
    CANONICAL_MANIFEST_DIGEST_PATH,
    CANONICAL_MANIFEST_PATH,
    DEFAULT_PROTOCOL_ROOT,
    ProtocolBundle,
    ProtocolError,
    build_manifest,
    load_bundle,
    manifest_drift,
    promote_documents,
    return_to_draft,
    rewrite_scalars,
    write_manifest,
)
from attention_sink.pilot.snapshots import ArmCycleSnapshot
from attention_sink.protocol import current_version

__all__ = [
    "BUDGET_HEADROOM_RATIO",
    "BUDGET_ROUNDING",
    "build_canonical_manifest",
    "build_run",
    "calibrate",
    "canonical_launch_mismatches",
    "counter_identity",
    "main",
    "model_specs",
    "proposed_budget",
    "read_canonical_manifest",
    "require_canonical_launch",
    "write_canonical_manifest",
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


def calibrate(
    bundle: ProtocolBundle, counter: ExactTokenCounter, *, token_count_source: str | None = None
) -> tuple[int, int]:
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
                "provisional_token_count": str(counts[seed.memory_id]),
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
    # The source travels with the version because the version alone cannot tell a
    # reader whether `heuristic-v1` was a fixture run's only option or a deployment's
    # declared choice, and a protocol that named the wrong one would be the single
    # field nobody could check.
    rewrite_scalars(
        root / bundle.paths[0],
        {"memory_budget_tokens": str(budget), "counter_version": counter.version}
        | ({} if token_count_source is None else {"token_count_source": token_count_source}),
    )
    return total, budget


# --------------------------------------------------------------------- wiring


COUNTER_SOURCE_NAMES: dict[str, str] = {
    BEDROCK_COUNTER_VERSION: "bedrock_count_tokens",
    CONVERSE_COUNTER_VERSION: "bedrock_converse_usage",
}
"""What a run records about each exact counter, keyed by the counter's own version.

Only these two names are in :data:`EXACT_TOKEN_COUNT_SOURCES`. The approximate counter
is not listed because its recorded name depends on the run rather than the counter:
the same heuristic is a fixture's only option locally and a declared choice on AWS.
"""


def counter_identity(gateway: ModelGateway) -> tuple[str, str]:
    """The counter version and source name the built gateway actually counts with.

    Read off the gateway for the same reason :func:`model_specs` is: a protocol
    declares what a canonical run should use, and a run has to record what it did use.
    A frozen protocol run locally against fixture models is the case that makes the
    difference visible.
    """
    version = str(gateway.token_counter.version)
    approximate = "local_fixture_heuristic" if gateway.simulated else "approximate_heuristic"
    return version, COUNTER_SOURCE_NAMES.get(version, approximate)


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
    run_kind: RunKind = RunKind.LOCAL_FIXTURE,
    now: datetime | None = None,
) -> PilotEngine:
    """Build an initialised engine for ``bundle``, ready to run cycle 1.

    Raises:
        ProtocolError: The bundle is not validated, has drifted, or is uncalibrated.
    """
    bundle.require_runnable(canonical=run_kind.is_canonical)
    resolved = gateway if gateway is not None else build_gateway(GatewaySettings.from_env())
    writer, embedding = model_specs(resolved)
    counter_version, token_count_source = counter_identity(resolved)
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
        run_kind=run_kind,
        counter_version=counter_version,
        token_count_source=token_count_source,
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
        if document.is_digested and document.content_hash != recomputed:
            mark = "MODIFIED SINCE VALIDATION"
        print(f"  {name:<48} {state:<16} {mark}")
        if mark != "ok":
            print(f"      recorded:   {document.content_hash or '(none)'}")
            print(f"      recomputed: {recomputed}")
    calibrated = bundle.protocol.is_calibrated and bundle.seed_world.is_calibrated
    print(f"  seeds: {len(bundle.seed_world.memories)}", end="")
    print(f"  stimuli: {len(bundle.stimulus_deck.stimuli)}", end="")
    print(f"  facts: {len(bundle.truth_ledger.facts)}", end="")
    print(f"  questions: {len(bundle.interview.questions)}")
    print(f"  budget: {bundle.protocol.memory_budget_tokens} ", end="")
    print(f"({bundle.protocol.token_count_source})")
    print(f"  calibrated: {calibrated}  local_validated: {bundle.is_local_validated}", end="")
    print(f"  frozen: {bundle.is_frozen}")

    drifted = bundle.drifted()
    if drifted:
        print(f"FAILED: modified after validation: {', '.join(drifted)}", file=sys.stderr)
        return 1
    if bundle.is_local_validated:
        stale = manifest_drift(bundle, prompt_hashes=_prompt_hashes(bundle))
        if stale:
            print(f"  manifest                                         STALE: {', '.join(stale)}")
            print(f"FAILED: manifest disagrees with: {', '.join(stale)}", file=sys.stderr)
            return 1
        print("  manifest                                         ok")
    return 0


def _prompt_hashes(bundle: ProtocolBundle) -> dict[str, str]:
    """Every prompt digest the manifest covers, read from a fixture gateway.

    Built from the gateway rather than from the filesystem so the manifest records
    the templates the run would actually load, resolved the way the run resolves
    them.
    """
    library = build_gateway(GatewaySettings.from_env(env={})).prompts
    version = bundle.protocol.writer_prompt_version
    return {t.identifier: t.digest for t in library.manifest(version)} | {
        "prompt_set": library.prompt_set_digest(version)
    }


def _command_calibrate(args: argparse.Namespace) -> int:
    bundle = _load(args)
    gateway = build_gateway(GatewaySettings.from_env())
    _, source = counter_identity(gateway)
    total, budget = calibrate(bundle, gateway.token_counter, token_count_source=source)
    print(f"counter:      {gateway.token_counter.version}")
    print(f"seed tokens:  {total}")
    print(f"budget:       {budget}  ({BUDGET_HEADROOM_RATIO}x, rounded to {BUDGET_ROUNDING})")
    print("re-validating")
    return _command_validate(args)


def _command_local_validate(args: argparse.Namespace) -> int:
    bundle = _load(args)
    written = promote_documents(bundle)
    if not written:
        print("already local-validated; nothing to write")
    for name in written:
        print(f"validated {name}")
    reloaded = _load(args)
    path = write_manifest(reloaded, prompt_hashes=_prompt_hashes(reloaded))
    print(f"wrote {path}")
    return _command_validate(args)


def _command_draft(args: argparse.Namespace) -> int:
    """Return every document to DRAFT so the protocol can be edited again."""
    written = return_to_draft(_load(args))
    if not written:
        print("already a draft; nothing to write")
    for name in written:
        print(f"returned {name} to draft")
    return _command_validate(args)


def _command_run(args: argparse.Namespace) -> int:
    bundle = _load(args)
    run_kind = RunKind(args.run_kind)
    engine = build_run(bundle, run_id=args.run_id, run_kind=run_kind)
    configuration = engine.configuration
    if configuration.simulated:
        print("SIMULATED - LOCAL - NON-CANONICAL: no model produced anything in this run.")
        print("These results describe application behaviour, not model behaviour.")
    print(f"run kind: {configuration.run_kind.value}")
    print(f"run {configuration.run_id}: {len(configuration.arms)} arms, ", end="")
    print(f"{configuration.memory_budget_tokens} token budget, ", end="")
    print(f"{configuration.maximum_cycles} cycles")

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
        ("local-validate", _command_local_validate),
        ("draft", _command_draft),
    ):
        subcommands.add_parser(name).set_defaults(handler=handler)

    run = subcommands.add_parser("run")
    run.add_argument("--cycles", type=int, default=24)
    run.add_argument("--run-id", default="pilot_local")
    run.add_argument("--out", type=Path, default=None)
    run.add_argument(
        "--run-kind", default=RunKind.LOCAL_FIXTURE.value, choices=[k.value for k in RunKind]
    )
    run.set_defaults(handler=_command_run)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one pilot command.

    Returns:
        A process exit status. A protocol failure or a refused run configuration
        returns 1 with a message on stderr rather than a traceback: a draft protocol,
        and an operator asking a fixture gateway for a canonical run, are both
        ordinary states rather than crashes.
    """
    args = _parser().parse_args(argv)
    try:
        exit_status: int = args.handler(args)
    except (ProtocolError, ValueError) as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return exit_status


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())


# ------------------------------------------------------- the canonical manifest


def build_canonical_manifest(
    bundle: ProtocolBundle,
    *,
    prompt_hashes: Mapping[str, str],
    settings: GatewaySettings,
    analysis: Mapping[str, Any],
) -> dict[str, Any]:
    """Everything the frozen protocol defines, in one document.

    The protocol manifest records digests, and answers "have these files changed".
    This one records the experiment, and answers "is the run about to start the one
    that was frozen": the models, the inference settings, every policy parameter, the
    metric versions, the commit. A launch check compares a proposed run configuration
    against it field by field, so anything absent here is something a run could
    change without being refused.

    ``analysis`` carries the metric constants. They are passed in rather than
    imported because the pilot may not import the analysis package -- see the import
    boundary test -- and a manifest that omitted them would let a metric definition
    change under a frozen protocol.
    """
    protocol = bundle.protocol
    policies = protocol.policies
    models = settings.models
    inference = settings.inference
    pinned_seed = policies.pinned_origin.pinned_seed_memory_id
    position = next(
        memory.initial_position
        for memory in bundle.seed_world.memories
        if memory.memory_id == pinned_seed
    )
    version = current_version()
    return {
        "schema_version": 1,
        "protocol": build_manifest(bundle, prompt_hashes=prompt_hashes),
        "seed_world": {
            "version": str(protocol.seed_world_version),
            "memories": len(bundle.seed_world.memories),
            "content_hashes": {
                memory.memory_id: memory.expected_content_hash
                for memory in bundle.seed_world.memories
            },
        },
        "stimuli": {
            "version": str(protocol.stimulus_deck_version),
            "count": len(bundle.stimulus_deck.stimuli),
            "content_hashes": {
                stimulus.stimulus_id: stimulus.expected_content_hash
                for stimulus in bundle.stimulus_deck.stimuli
            },
        },
        "truth_ledger": {
            "version": str(protocol.truth_ledger_version),
            "facts": len(bundle.truth_ledger.facts),
        },
        "interview": {
            "version": str(protocol.interview_version),
            "questions": len(bundle.interview.questions),
        },
        "run_shape": {
            "maximum_cycles": protocol.maximum_cycles,
            "checkpoint_cycles": list(protocol.checkpoint_cycles),
            "arms": [arm.value for arm in protocol.arms],
        },
        "budget": {
            "memory_budget_tokens": protocol.memory_budget_tokens,
            "counter_version": str(protocol.counter_version),
            "token_count_source": protocol.token_count_source,
            "calibration_writer_model_id": protocol.calibration_writer_model_id,
            "calibration_region": protocol.calibration_region,
            "calibrated_at": (
                None if protocol.calibrated_at is None else protocol.calibrated_at.isoformat()
            ),
            "calibration_input_hashes": dict(sorted(protocol.calibration_input_hashes.items())),
        },
        "models": {
            "region": None if models is None else models.region,
            "writer_model_id": None if models is None else models.writer_model_id,
            "auditor_model_id": None if models is None else models.auditor_model_id,
            "summary_model_id": None if models is None else models.summary_model_id,
            # The interview is the same agent answering questions, so it is the
            # writer's model. Recorded separately anyway: a reader should not have to
            # know that rule to know which model answered.
            "interview_model_id": None if models is None else models.writer_model_id,
            "evaluator_model_id": None if models is None else models.judge_model_id,
            "embedding_model_id": None if models is None else models.embedding_model_id,
        },
        "inference": {
            "temperature": inference.temperature,
            "top_p": inference.top_p,
            "writer_max_tokens": inference.writer_max_tokens,
            "summary_max_tokens": inference.summary_max_tokens,
            "max_model_retries": settings.max_model_retries,
            "request_timeout_seconds": settings.request_timeout_seconds,
        },
        "prompts": {
            "writer_prompt_version": str(protocol.writer_prompt_version),
            "summary_prompt_version": str(protocol.summary_prompt_version),
            "hashes": dict(sorted(prompt_hashes.items())),
        },
        "policies": {
            "fifo": {"version": str(policies.fifo.version)},
            "lru": {"version": str(policies.lru.version)},
            "heavy_hitter": {
                "version": str(policies.heavy_hitter.version),
                "citation_decay": policies.heavy_hitter.citation_decay,
                "recency_reserve": policies.heavy_hitter.recency_reserve,
            },
            "pinned_origin": {
                "version": str(policies.pinned_origin.version),
                "pinned_seed_memory_id": pinned_seed,
                "pinned_memory_id": make_memory_id(ArmId.ARM_SINK, position - 1),
            },
            "seeded_random": {
                "version": str(policies.seeded_random.version),
                "random_seed": policies.seeded_random.random_seed,
            },
            "dreamer": {
                "version": str(policies.dreamer.version),
                "target_summary_tokens": policies.dreamer.target_summary_tokens,
                "safety_margin_tokens": policies.dreamer.safety_margin_tokens,
                "min_sources": policies.dreamer.min_sources,
                "fallback_rule": policies.dreamer.fallback_rule,
            },
        },
        "spending": {
            "citation_mode": protocol.citation_mode.value,
            "model_call_limits": protocol.model_call_limits.model_dump(mode="json"),
            "max_parallel_model_calls": protocol.max_parallel_model_calls,
        },
        "analysis": dict(sorted(analysis.items())),
        "application": {
            "app_version": version.app_version,
            "git_commit": version.git_commit,
        },
    }


def _manifest_changes(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[str]:
    """The dotted paths at which two canonical manifests disagree.

    Only deep enough to name the field an operator has to look at. `content_hash`
    changes whenever anything else does, so reporting it alone would say a manifest
    changed without saying what changed.
    """
    changes: list[str] = []
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        if isinstance(old, Mapping) and isinstance(new, Mapping):
            changes.extend(f"{key}.{inner}" for inner in _manifest_changes(old, new))
        else:
            changes.append(key)
    return [change for change in changes if change != "content_hash"]


def write_canonical_manifest(
    bundle: ProtocolBundle,
    *,
    prompt_hashes: Mapping[str, str],
    settings: GatewaySettings,
    analysis: Mapping[str, Any],
) -> tuple[Path, Path, str]:
    """Write the canonical manifest and its digest beside the protocol.

    The digest is written to its own file in ``sha256sum`` format so that verifying
    it needs no code from this repository. A reader who does not trust the tooling
    can check the freeze with one shell command.

    Returns:
        The manifest path, the digest path, and the digest.

    Raises:
        ProtocolError: The protocol is frozen and this would change the manifest that
            is already there.
    """
    manifest = build_canonical_manifest(
        bundle, prompt_hashes=prompt_hashes, settings=settings, analysis=analysis
    )
    manifest["content_hash"] = canonical_digest(manifest)
    rendered = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    path = bundle.root / CANONICAL_MANIFEST_PATH
    # A frozen manifest is what a canonical run is bound to: the run records its hash,
    # and every launch is refused unless the manifest still hashes to it. Rewriting it
    # after the freeze does not re-freeze anything -- it silently invalidates the run
    # that is already bound to the old hash. Re-running the freeze is a reasonable
    # thing for an operator to do, so this refuses the change rather than the command,
    # and identical content stays a no-op. Nothing lifts it: the constitution says no
    # canonical result may be edited, and this is the file that says what canonical is.
    if bundle.is_frozen and path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing != rendered:
            changed = ", ".join(_manifest_changes(json.loads(existing), manifest)) or "its content"
            msg = (
                f"{path} is frozen and this would change {changed}; "
                "retire the protocol and freeze a new one rather than editing this"
            )
            raise ProtocolError(msg)
    path.write_text(rendered, encoding="utf-8")
    digest = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    digest_path = bundle.root / CANONICAL_MANIFEST_DIGEST_PATH
    digest_path.write_text(f"{digest}  {CANONICAL_MANIFEST_PATH}\n", encoding="utf-8")
    return path, digest_path, digest


def read_canonical_manifest(root: Path = DEFAULT_PROTOCOL_ROOT) -> dict[str, Any]:
    """Load the frozen protocol's canonical manifest, checking its own digest first.

    Raises:
        ProtocolError: The manifest or its digest file is missing, the file does not
            hash to the recorded digest, or its recorded ``content_hash`` no longer
            matches its content.
    """
    path = root / CANONICAL_MANIFEST_PATH
    digest_path = root / CANONICAL_MANIFEST_DIGEST_PATH
    if not path.is_file() or not digest_path.is_file():
        msg = f"no canonical manifest at {path}; run `make pilot-freeze`"
        raise ProtocolError(msg)
    rendered = path.read_text(encoding="utf-8")
    recorded = digest_path.read_text(encoding="utf-8").split()[0]
    recomputed = hashlib.sha256(rendered.encode("utf-8")).hexdigest()
    if recorded != recomputed:
        msg = f"{path} does not match {digest_path}: {recomputed} is not {recorded}"
        raise ProtocolError(msg)
    manifest = json.loads(rendered)
    if not isinstance(manifest, dict):
        msg = f"{path} must contain a JSON object"
        raise ProtocolError(msg)
    claimed = manifest.pop("content_hash", None)
    if canonical_digest(manifest) != claimed:
        msg = f"{path} has been edited: its recorded content_hash no longer matches"
        raise ProtocolError(msg)
    manifest["content_hash"] = claimed
    return manifest


def canonical_launch_mismatches(
    configuration: PilotRunConfiguration, manifest: Mapping[str, Any]
) -> tuple[str, ...]:
    """Every way a proposed run differs from the frozen protocol it claims to be.

    Compares the run configuration field by field against the canonical manifest,
    rather than checking a single digest over the two. A digest answers "are these
    the same" and stops there; a canonical run that is refused should say which of
    the twenty things it changed, because the operator who set the wrong model
    identifier needs to be told that and not told "hashes differ".

    Returns:
        One line per difference, in a fixed order. Empty when the run is the frozen
        experiment.
    """
    protocol = manifest.get("protocol", {})
    budget = manifest.get("budget", {})
    models = manifest.get("models", {})
    prompts = manifest.get("prompts", {})
    policies = manifest.get("policies", {})
    shape = manifest.get("run_shape", {})
    checks: list[tuple[str, Any, Any]] = [
        ("protocol version", protocol.get("protocol_version"), str(configuration.protocol_version)),
        ("memory budget", budget.get("memory_budget_tokens"), configuration.memory_budget_tokens),
        ("counter version", budget.get("counter_version"), str(configuration.counter_version)),
        ("token count source", budget.get("token_count_source"), configuration.token_count_source),
        ("writer model", models.get("writer_model_id"), configuration.writer_model.model_id),
        (
            "embedding model",
            models.get("embedding_model_id"),
            configuration.embedding_model.model_id,
        ),
        ("region", models.get("region"), configuration.writer_model.region),
        (
            "writer prompt version",
            prompts.get("writer_prompt_version"),
            str(configuration.writer_prompt_version),
        ),
        (
            "summary prompt version",
            prompts.get("summary_prompt_version"),
            str(configuration.summary_prompt_version),
        ),
        (
            "prompt set digest",
            prompts.get("hashes", {}).get("prompt_set"),
            configuration.prompt_set_digest,
        ),
        ("maximum cycles", shape.get("maximum_cycles"), configuration.maximum_cycles),
        (
            "checkpoint cycles",
            shape.get("checkpoint_cycles"),
            list(configuration.checkpoint_cycles),
        ),
        ("arms", shape.get("arms"), [arm.value for arm in configuration.arms]),
        (
            "heavy-hitter citation decay",
            policies.get("heavy_hitter", {}).get("citation_decay"),
            configuration.heavy_hitter_citation_decay,
        ),
        (
            "heavy-hitter recency reserve",
            policies.get("heavy_hitter", {}).get("recency_reserve"),
            configuration.heavy_hitter_recency_reserve,
        ),
        (
            "pinned memory",
            policies.get("pinned_origin", {}).get("pinned_memory_id"),
            configuration.pinned_origin_memory_id,
        ),
        (
            "random seed",
            policies.get("seeded_random", {}).get("random_seed"),
            configuration.random_seed,
        ),
        (
            "Dreamer target summary tokens",
            policies.get("dreamer", {}).get("target_summary_tokens"),
            configuration.dreamer_target_summary_tokens,
        ),
        (
            "Dreamer safety margin",
            policies.get("dreamer", {}).get("safety_margin_tokens"),
            configuration.dreamer_safety_margin_tokens,
        ),
        (
            "Dreamer minimum sources",
            policies.get("dreamer", {}).get("min_sources"),
            configuration.dreamer_min_sources,
        ),
    ]
    differences = [
        f"{label}: frozen as {frozen!r}, proposed as {proposed!r}"
        for label, frozen, proposed in checks
        if frozen != proposed
    ]
    # Over the configuration's own files rather than the manifest's. The manifest
    # also digests `predictions.md`, which a run configuration does not carry because
    # it is prose that cannot change what a cycle does; `pilot validate` checks that
    # one against the protocol manifest, where it belongs.
    frozen_files: Mapping[str, str] = protocol.get("files", {})
    for name, proposed in sorted(configuration.protocol_content_hashes.items()):
        frozen = frozen_files.get(name)
        if frozen != proposed:
            differences.append(f"{name}: frozen as {frozen!r}, proposed as {proposed!r}")
    return tuple(differences)


def require_canonical_launch(
    configuration: PilotRunConfiguration, manifest: Mapping[str, Any]
) -> None:
    """Refuse a canonical run that is not the experiment that was frozen.

    Raises:
        ProtocolError: The run differs from the canonical manifest in any recorded
            field. Nothing has been created when this raises.
    """
    differences = canonical_launch_mismatches(configuration, manifest)
    if not differences:
        return
    listed = "\n  ".join(differences)
    msg = (
        f"refusing to launch {configuration.run_id} as canonical: it is not the "
        f"experiment {manifest.get('protocol', {}).get('protocol_version')} was frozen "
        f"as.\n  {listed}"
    )
    raise ProtocolError(msg)
