"""Seeded random eviction: the control with a mechanism but no criterion."""

from __future__ import annotations

import random
from dataclasses import dataclass

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision, RandomDraw, RandomProvenance
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.hashing import selection_digest
from attention_sink.domain.memory import Memory
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import Candidate, build_decision, eligible_memories

__all__ = ["SeededRandomPolicy"]


@dataclass(frozen=True, slots=True)
class SeededRandomPolicy:
    """Evicts uniformly at random from entropy the application controls and records.

    This is the arm that says what forgetting costs when nothing is being optimised
    for -- the floor a mechanism has to beat to have earned its complexity.

    Randomness is never the model's and never the operating system's. Each choice
    derives its own seed from ``SHA-256(run seed | arm | cycle | decision index |
    sorted candidates)``, so a decision replays from the manifest alone, two arms
    sharing a run seed still diverge, and no module-global generator can be
    perturbed by unrelated code running in the same process.
    """

    arm_id: ArmId = ArmId.ARM_RANDOM
    policy_version: str = "random-v1"

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Draw memories to retire until the arm is within budget."""
        remaining = {memory.memory_id: memory for memory in eligible_memories(state, context)}
        tokens_after = state.active_tokens
        victims: list[Memory] = []
        draws: list[RandomDraw] = []
        drawn: list[Candidate] = []

        while remaining and not budget.is_satisfied_by(tokens_after):
            decision_index = len(draws)
            candidate_ids = sorted(remaining)
            digest = selection_digest(
                run_random_seed=context.run_random_seed,
                arm_id=context.arm_id.value,
                cycle=context.cycle,
                decision_index=decision_index,
                candidate_memory_ids=candidate_ids,
            )
            # Reproducibility, not unpredictability: the Mersenne Twister is the right
            # tool here precisely because it is stable and replayable across machines.
            rng = random.Random(int(digest, 16))  # noqa: S311
            selected_index = rng.randrange(len(candidate_ids))
            selected_id = candidate_ids[selected_index]
            selected = remaining.pop(selected_id)

            tokens_after -= selected.token_count
            victims.append(selected)
            draws.append(
                RandomDraw(
                    decision_index=decision_index,
                    digest=digest,
                    candidate_memory_ids=tuple(candidate_ids),
                    selected_index=selected_index,
                    selected_memory_id=selected_id,
                )
            )
            drawn.append(
                (
                    selected,
                    f"draw={decision_index};selected_index={selected_index};"
                    f"digest={digest[:16]};memory_id={selected_id}",
                )
            )

        undrawn: list[Candidate] = [
            (remaining[memory_id], f"draw=none;memory_id={memory_id}")
            for memory_id in sorted(remaining)
        ]
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.EVICTED_RANDOM
            if victims
            else PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET,
            candidates=[*drawn, *undrawn],
            victims=victims,
            tokens_after=tokens_after,
            random_provenance=RandomProvenance(
                run_random_seed=context.run_random_seed, draws=tuple(draws)
            )
            if draws
            else None,
        )
