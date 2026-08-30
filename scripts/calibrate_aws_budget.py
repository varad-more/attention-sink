#!/usr/bin/env python
"""Derive the canonical active-memory budget from the model that will read it.

Ten measurements and three feasibility checks, all against real Bedrock models
(ADR-011, ADR-013). Writes the counts and the calibration provenance back into the
protocol files and records the derivation in ``docs/pilot/aws-token-calibration.md``.

Unlike ``calibrate_local_budget.py``, nothing here is provisional. The counter is the
writer model's own tokeniser, so the number this produces is the one a canonical run
is denominated in, and it is the only number this protocol may be frozen around.

    ALLOW_BEDROCK_CALLS=1 MODEL_MODE=bedrock TOKEN_COUNT_SOURCE=converse
    python scripts/calibrate_aws_budget.py [--root experiment/pilot] [--samples 3]

Costs a handful of model calls: one writer call per sample, one summarizer call, and
one counting call per distinct text.
"""

from __future__ import annotations

import argparse
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from attention_sink.domain import CompressionPlan, MemoryKind, MemoryState, make_memory_id
from attention_sink.model_gateway import (
    GatewaySettings,
    ModelGateway,
    TokenCountSource,
    build_gateway,
    build_writer_request,
    present_memories,
)
from attention_sink.pilot import ProtocolBundle, calibrate, load_bundle
from attention_sink.pilot.cli import BUDGET_HEADROOM_RATIO, BUDGET_ROUNDING, counter_identity
from attention_sink.pilot.protocol import (
    DEFAULT_PROTOCOL_ROOT,
    EXACT_TOKEN_COUNT_SOURCES,
    ProtocolError,
    rewrite_block,
    rewrite_scalars,
)

CALIBRATION_DOC = Path("docs/pilot/aws-token-calibration.md")

CALIBRATION_INPUTS = ("seed_memories.yaml", "stimuli.yaml")
"""Files whose content the budget is derived from, digested at the moment it is.

The truth ledger and the interview questions are not here: neither reaches a writer
request, so neither can move a token count. Neither is `protocol.yaml`, which is where
the *result* is written -- a file cannot record a digest of itself that includes the
digest, and pretending otherwise would produce a hash that never verifies.
"""

MINIMUM_PRESSURE_CYCLES = 2
MAXIMUM_PRESSURE_CYCLES = 10
"""The window the first eviction must fall inside, in cycles of generated memory.

Below the floor no arm establishes itself on identical seed state before its
mechanism starts removing that state, and every arm is effectively a stateless arm.
Above the ceiling the budget binds too late for the remaining cycles to show what
the mechanisms do differently. The pilot targets roughly five.
"""


@dataclass(frozen=True, slots=True)
class Measurements:
    """Everything step 2 measures, in the units the budget will be denominated in."""

    per_seed: dict[str, int]
    seed_total: int
    block_tokens: int
    writer_request_tokens: int
    generated_samples: tuple[int, ...]
    summary_samples: tuple[int, ...]

    @property
    def generated_tokens(self) -> int:
        """What one generated memory costs, taken as the largest sample.

        The largest rather than the mean: the budget has to hold whatever the writer
        actually produces, and a mean that admits half the samples is not a ceiling.
        """
        return max(self.generated_samples)

    @property
    def summary_tokens(self) -> int:
        """What one Dreamer summary costs, taken the same way."""
        return max(self.summary_samples)


def seed_state(bundle: ProtocolBundle) -> MemoryState:
    """The six arms' shared starting state, as every arm really begins it."""
    state = MemoryState(run_id="calibration", arm_id=bundle.protocol.arms[0])
    for seed in bundle.seed_world.memories:
        state = state.admit(
            [
                state.mint(
                    text=seed.text,
                    token_count=seed.provisional_token_count or 1,
                    memory_kind=MemoryKind.SEED,
                    cycle=0,
                )
            ]
        )
    return state


def measure(bundle: ProtocolBundle, gateway: ModelGateway, *, samples: int) -> Measurements:
    """Steps 1 to 5: count the seed world, the request around it, and what fills it."""
    counter = gateway.token_counter
    seeds = bundle.seed_world.memories
    per_seed = {seed.memory_id: counter.count(seed.text) for seed in seeds}

    state = seed_state(bundle)
    block = counter.count(present_memories(state.active_memories).block)

    stimuli = bundle.stimulus_deck.stimuli
    request = build_writer_request(
        gateway.prompts,
        cycle=1,
        stimulus_text=stimuli[0].text,
        active_memories=state.active_memories,
        version=bundle.protocol.writer_prompt_version,
    )
    request_tokens = counter.count_request(system=request.system, user=request.user).tokens

    generated: list[int] = []
    for stimulus in stimuli[:samples]:
        result = gateway.writer.write(
            cycle=1, stimulus_text=stimulus.text, active_memories=state.active_memories
        )
        generated.append(counter.count(result.output.candidate_memory))

    dreamer = bundle.protocol.policies.dreamer
    sources = tuple(state.active_memories[: dreamer.min_sources])
    plan = CompressionPlan(
        source_memory_ids=tuple(memory.memory_id for memory in sources),
        summary_memory_id=make_memory_id(bundle.protocol.arms[0], len(state.active_memories)),
        summary_target_token_limit=dreamer.target_summary_tokens,
        tokens_freed=sum(memory.token_count for memory in sources),
        safety_margin_tokens=dreamer.safety_margin_tokens,
    )
    summary = gateway.summarizer.summarize(plan=plan, sources=sources)
    summaries = [counter.count(summary.output.summary_text)]

    return Measurements(
        per_seed=per_seed,
        seed_total=sum(per_seed.values()),
        block_tokens=block,
        writer_request_tokens=request_tokens,
        generated_samples=tuple(generated),
        summary_samples=tuple(summaries),
    )


def select_budget(measured: Measurements) -> int:
    """Step 6: the budget the measured seed set implies, on the declared rule."""
    target = measured.seed_total * BUDGET_HEADROOM_RATIO
    return int(-(-target // BUDGET_ROUNDING) * BUDGET_ROUNDING)


def check_feasible(bundle: ProtocolBundle, measured: Measurements, budget: int) -> tuple[str, ...]:
    """Steps 7 to 9, as refusals rather than warnings.

    Returns:
        One line per check, in the order the brief states them.

    Raises:
        ProtocolError: A check failed. A budget that cannot hold the pinned memory, or
            that leaves no cycle under pressure, or that no legal summary fits inside,
            is a budget that would make the experiment measure the wrong thing.
    """
    lines: list[str] = []
    headroom = budget - measured.seed_total
    cycles = headroom // measured.generated_tokens if measured.generated_tokens else 0
    if not MINIMUM_PRESSURE_CYCLES <= cycles <= MAXIMUM_PRESSURE_CYCLES:
        msg = (
            f"a {budget}-token budget over a {measured.seed_total}-token seed set leaves "
            f"{headroom} tokens, about {cycles} cycles of generated memory. Divergence "
            f"needs the first eviction between cycles {MINIMUM_PRESSURE_CYCLES} and "
            f"{MAXIMUM_PRESSURE_CYCLES}."
        )
        raise ProtocolError(msg)
    lines.append(f"memory pressure first binds at about cycle {cycles} (target 2-10)")

    pinned_id = bundle.protocol.policies.pinned_origin.pinned_seed_memory_id
    pinned = measured.per_seed[pinned_id]
    if pinned >= budget:
        msg = f"the pinned memory {pinned_id} costs {pinned} tokens and the budget is {budget}"
        raise ProtocolError(msg)
    lines.append(f"the pinned memory {pinned_id} costs {pinned} of {budget} tokens and always fits")

    dreamer = bundle.protocol.policies.dreamer
    limit = dreamer.target_summary_tokens
    if measured.summary_tokens > limit:
        msg = (
            f"the summarizer produced {measured.summary_tokens} tokens against a "
            f"{limit}-token target; no legal summary would be admitted"
        )
        raise ProtocolError(msg)
    smallest = sorted(measured.per_seed.values())[: dreamer.min_sources]
    if measured.summary_tokens >= sum(smallest):
        msg = (
            f"a {measured.summary_tokens}-token summary of the {dreamer.min_sources} "
            f"cheapest memories ({sum(smallest)} tokens) would free nothing"
        )
        raise ProtocolError(msg)
    lines.append(
        f"a real summary cost {measured.summary_tokens} tokens against a {limit}-token "
        f"target, and frees space over {dreamer.min_sources} sources"
    )
    return tuple(lines)


def markdown_table(header: tuple[str, ...], rows: tuple[tuple[str, ...], ...]) -> str:
    """Render a column-aligned Markdown table, the way Prettier would format it."""
    widths = [max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)]
    return "\n".join(
        "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + " |"
        for row in (header, tuple("-" * width for width in widths), *rows)
    )


def write_calibration_doc(
    *,
    bundle: ProtocolBundle,
    measured: Measurements,
    budget: int,
    checks: tuple[str, ...],
    model_id: str,
    region: str,
    counter_version: str,
    source: str,
    calibrated_at: datetime,
    digests: dict[str, str],
) -> Path:
    """Step 10: record what was measured, what was derived, and against what."""
    per_seed = markdown_table(
        ("seed", "tokens"),
        tuple((f"`{seed}`", str(count)) for seed, count in measured.per_seed.items()),
    )
    derived = markdown_table(
        ("quantity", "tokens", "how"),
        (
            ("seed set", str(measured.seed_total), "sum of the twelve seed texts"),
            (
                "serialized seed block",
                str(measured.block_tokens),
                "as rendered, labels and separators included",
            ),
            (
                "complete writer request",
                str(measured.writer_request_tokens),
                "system and user turns, cycle 1, stimulus 1",
            ),
            (
                "generated memory",
                str(measured.generated_tokens),
                f"largest of {len(measured.generated_samples)} real writer calls "
                f"({', '.join(str(n) for n in measured.generated_samples)})",
            ),
            (
                "Dreamer summary",
                str(measured.summary_tokens),
                f"largest of {len(measured.summary_samples)} real summarizer calls "
                f"({', '.join(str(n) for n in measured.summary_samples)})",
            ),
            (
                "**active-memory budget**",
                f"**{budget}**",
                f"{measured.seed_total} scaled by {BUDGET_HEADROOM_RATIO}, "
                f"rounded up to {BUDGET_ROUNDING}s",
            ),
        ),
    )
    inputs = markdown_table(
        ("file", "digest at calibration"),
        tuple((f"`{name}`", f"`{digests[name]}`") for name in CALIBRATION_INPUTS),
    )
    provenance = markdown_table(
        ("field", "value"),
        (
            ("writer model", f"`{model_id}`"),
            ("Region", f"`{region}`"),
            ("token count source", f"`{source}`"),
            ("counter version", f"`{counter_version}`"),
            ("calibrated at", f"`{calibrated_at.isoformat().replace('+00:00', 'Z')}`"),
            ("protocol version", f"`{bundle.protocol.protocol_version}`"),
        ),
    )
    checklist = "\n".join(f"- {line}" for line in checks)
    CALIBRATION_DOC.parent.mkdir(parents=True, exist_ok=True)
    CALIBRATION_DOC.write_text(
        f"""# AWS token calibration — CANONICAL

Generated by `scripts/calibrate_aws_budget.py`. Do not edit by hand; re-run the
script instead.

Every number on this page was produced by `{model_id}`'s own tokeniser, reported by
the provider for exactly the text that was measured. This is the unit the canonical
run's active-memory budget is denominated in, and the only unit this protocol may be
frozen around (ADR-011, ADR-013). It supersedes
[`local-token-calibration.md`](local-token-calibration.md), whose numbers came from
the deterministic local heuristic and were always marked provisional.

## Provenance

{provenance}

## Calibration inputs

The budget is derived from these files as they stood at the moment above. A protocol
whose seed world has changed since is a protocol for a different experiment, and
these digests are what a reader checks that against.

{inputs}

## Per-seed cost

{per_seed}

## Derived quantities

{derived}

## Feasibility

A budget is only useful if the mechanisms it constrains can actually run inside it.
Each of these is checked before the budget is written, and a failure refuses the
calibration rather than reporting a warning.

{checklist}

## What the budget was chosen to do

The budget is {budget} tokens against a {measured.seed_total}-token seed set. The
headroom is deliberately small: about
{(budget - measured.seed_total) // measured.generated_tokens} cycles of generated
memory before any arm must retire anything, out of twenty-four.

That is the point. A budget that never binds makes all six arms identical, and a
budget that binds at cycle 1 means no arm establishes itself on the seed world before
the mechanism starts removing it. A few cycles of orientation leaves the rest of the
run under pressure, which is where the arms can differ.

The complete writer request costs {measured.writer_request_tokens} tokens — more than
the {measured.block_tokens}-token serialized block, which in turn costs more than the
{measured.seed_total}-token seed set, because a request pays for the instructions, the
`m1..mn` labels, and the separators as well as the text. The budget deliberately
governs _stored_ memory tokens rather than rendered prompt tokens: the mechanism
decides what an arm keeps, and what a prompt costs to render is a consequence of that
decision rather than an input to it (ADR-008).
""",
        encoding="utf-8",
    )
    return CALIBRATION_DOC


def main() -> int:
    """Calibrate against the production models and record the derivation."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PROTOCOL_ROOT)
    parser.add_argument(
        "--samples", type=int, default=3, help="real writer calls to size a generated memory from"
    )
    args = parser.parse_args()

    if os.environ.get("ALLOW_BEDROCK_CALLS") != "1":
        print("refusing to spend: set ALLOW_BEDROCK_CALLS=1 to calibrate", file=sys.stderr)
        return 2

    settings = GatewaySettings.from_env()
    if settings.token_count_source is TokenCountSource.HEURISTIC:
        print(
            "refusing to calibrate a canonical budget with the approximate counter; "
            "set TOKEN_COUNT_SOURCE to an exact one",
            file=sys.stderr,
        )
        return 2
    gateway = build_gateway(settings)
    if gateway.simulated:
        print("refusing to calibrate against fabricated generations", file=sys.stderr)
        return 2

    counter_version, source = counter_identity(gateway)
    if source not in EXACT_TOKEN_COUNT_SOURCES:
        print(f"{source} is not an exact counter", file=sys.stderr)
        return 2

    bundle = load_bundle(args.root)
    measured = measure(bundle, gateway, samples=args.samples)
    budget = select_budget(measured)
    checks = check_feasible(bundle, measured, budget)

    # Written before the digests are taken: the recorded hashes must describe the
    # files as a frozen protocol will carry them, not as they were a moment earlier.
    calibrate(bundle, gateway.token_counter, token_count_source=source)
    models = settings.models
    if models is None:  # pragma: no cover - forbidden by GatewaySettings in bedrock mode
        msg = "a bedrock gateway reached calibration with no model configuration"
        raise ProtocolError(msg)
    calibrated_at = datetime.now(UTC)
    rewrite_scalars(
        args.root / bundle.paths[0],
        {
            "calibration_writer_model_id": models.writer_model_id,
            "calibration_region": models.region,
            "calibrated_at": f"'{calibrated_at.isoformat().replace('+00:00', 'Z')}'",
        },
    )
    recalculated = load_bundle(args.root)
    digests = {name: recalculated.digests[name] for name in CALIBRATION_INPUTS}
    rewrite_block(args.root / bundle.paths[0], "calibration_input_hashes", digests)

    path = write_calibration_doc(
        bundle=recalculated,
        measured=measured,
        budget=budget,
        checks=checks,
        model_id=models.writer_model_id,
        region=models.region,
        counter_version=counter_version,
        source=source,
        calibrated_at=calibrated_at,
        digests=digests,
    )

    print(f"CANONICAL — counter {counter_version} ({source}) on {models.writer_model_id}")
    print(f"  seed set:                {measured.seed_total}")
    print(f"  serialized seed block:   {measured.block_tokens}")
    print(f"  complete writer request: {measured.writer_request_tokens}")
    print(f"  generated memory:        {measured.generated_tokens} {measured.generated_samples}")
    print(f"  Dreamer summary:         {measured.summary_tokens} {measured.summary_samples}")
    print(f"  active-memory budget:    {budget}")
    for line in checks:
        print(f"  ok  {line}")
    print(f"  wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
