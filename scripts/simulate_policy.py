"""Run one arm's mechanism against a JSON fixture and print the decision.

Uses the production domain and policy packages verbatim -- there is no simulator
model of the mechanism. A tool that reimplemented the policy in order to explain it
would eventually explain something the experiment does not do.

Usage:
    uv run python scripts/simulate_policy.py datasets/fixtures/policy_simulator/divergence.json
    make simulate FIXTURE=<path>

The fixture format is documented in docs/memory-policies.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from attention_sink.domain import (
    ArmId,
    CompressingMemoryPolicy,
    CycleContext,
    MemoryKind,
    MemoryState,
    PolicyConfiguration,
    PolicyDecision,
    TokenBudget,
    UnsatisfiableBudgetError,
)
from attention_sink.policies import policies_for


def build_state(arm: ArmId, fixture: dict[str, Any]) -> MemoryState:
    """Build one arm's memory from the fixture's shared memory list."""
    state = MemoryState(run_id=fixture["run_id"], arm_id=arm)
    for entry in fixture["memories"]:
        memory = state.mint(
            text=entry["text"],
            token_count=entry["token_count"],
            memory_kind=MemoryKind(entry.get("memory_kind", "generated")),
            cycle=entry["birth_cycle"],
            source_stimulus_id=entry.get("source_stimulus_id"),
            pinned=entry.get("pinned", False),
        )
        changes = {
            key: entry[key]
            for key in (
                "citation_count",
                "last_verified_citation_cycle",
                "discounted_citation_score",
            )
            if key in entry
        }
        state = state.admit([memory.evolve(**changes) if changes else memory])
    return state


def decide(arm: ArmId, fixture: dict[str, Any]) -> tuple[PolicyDecision, MemoryState]:
    """Run the arm's policy, supplying a summary if the mechanism asks for one."""
    policy = policies_for(PolicyConfiguration.model_validate(fixture.get("policies", {})))[arm]
    budget = TokenBudget.model_validate(fixture["budget"])
    context = CycleContext(
        run_id=fixture["run_id"],
        arm_id=arm,
        cycle=fixture["cycle"],
        stimulus_id=fixture["stimulus_id"],
        protocol_version=fixture["protocol_version"],
        prompt_version=fixture["prompt_version"],
        run_random_seed=fixture["run_random_seed"],
    )
    state = build_state(arm, fixture)
    decision = policy.rebalance(state, budget, context)
    while (plan := decision.compression_plan) is not None:
        if not isinstance(policy, CompressingMemoryPolicy):
            msg = f"{arm.value} requested a compression but implements no way to finalise one"
            raise TypeError(msg)
        if decision.retirements:
            state = state.apply(decision)
        # The simulator stands in for the summarising model with fixed text, and says
        # so in the output. It must never look like a generation that actually ran.
        summary = state.mint(
            text=f"[simulated summary of {len(plan.source_memory_ids)} memories]",
            token_count=min(
                plan.summary_target_token_limit, fixture.get("simulated_summary_tokens", 5)
            ),
            memory_kind=MemoryKind.SUMMARY,
            cycle=context.cycle,
            parent_memory_ids=plan.source_memory_ids,
        )
        decision = policy.finalize_compression(state, budget, context, plan, summary)
    return decision, state.apply(decision)


def main(argv: list[str] | None = None) -> int:
    """Print each requested arm's decision as JSON, and return a process exit code."""
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("fixture", type=Path, help="path to a policy fixture JSON file")
    parser.add_argument(
        "--arm",
        action="append",
        choices=[arm.value for arm in ArmId],
        help="arm to simulate; repeatable. Defaults to the fixture's arms.",
    )
    parser.add_argument(
        "--summary", action="store_true", help="print one line per arm instead of full JSON"
    )
    args = parser.parse_args(argv)

    fixture = json.loads(args.fixture.read_text(encoding="utf-8"))
    arms = [ArmId(value) for value in (args.arm or fixture.get("arms", [a.value for a in ArmId]))]

    failed = False
    results: dict[str, Any] = {"simulated": True, "fixture": str(args.fixture), "arms": {}}
    for arm in arms:
        try:
            decision, after = decide(arm, fixture)
        except UnsatisfiableBudgetError as error:
            failed = True
            results["arms"][arm.value] = {"error": str(error), "context": error.context}
            if args.summary:
                print(f"{arm.value:14} UNSATISFIABLE  {error.message}")
            continue
        results["arms"][arm.value] = {
            "decision": decision.model_dump(mode="json"),
            "active_after": list(after.active_memory_ids),
            "active_tokens_after": after.active_tokens,
        }
        if args.summary:
            print(
                f"{arm.value:14} {decision.decision_code.value:32} "
                f"{decision.tokens_before:>4} -> {decision.tokens_after:<4} "
                f"kept {len(decision.kept_memory_ids)}"
            )

    if not args.summary:
        print(json.dumps(results, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
