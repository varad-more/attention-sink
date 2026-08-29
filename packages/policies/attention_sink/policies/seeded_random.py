"""Seeded random eviction: the control that has a mechanism but no criterion."""

from __future__ import annotations

import random
from dataclasses import dataclass

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId, DecisionCode
from attention_sink.domain.rebalance import (
    RebalanceContext,
    RebalancePlan,
    derive_arm_cycle_seed,
)
from attention_sink.policies.base import Victim, evictable_entries, plan_from_victim_order

__all__ = ["SeededRandomPolicy"]


@dataclass(frozen=True, slots=True)
class SeededRandomPolicy:
    """Evicts uniformly at random from a seed derived from the recorded run seed.

    This is the arm that says what forgetting costs when nothing is being optimised
    for. Randomness is application-controlled, never the model's: the seed is a pure
    function of ``(run_seed, arm, cycle)``, is stored on every plan, and the shuffle
    runs over an origin-ordered list, so the same run replays to the same evictions.
    """

    arm_id: ArmId = ArmId.ARM_RANDOM
    policy_version: str = "random-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice memories in a seeded permutation of insertion order."""
        seed = derive_arm_cycle_seed(context.run_seed, context.arm_id, context.cycle_index)
        # Reproducibility, not unpredictability: Mersenne Twister is the right tool
        # here precisely because it is stable and replayable across machines.
        rng = random.Random(seed)  # noqa: S311
        pool = list(evictable_entries(candidate, context))
        order = rng.sample(pool, k=len(pool))
        victims: list[Victim] = [
            (entry, f"seed={seed};draw={position};origin_ordinal={entry.origin_ordinal}")
            for position, entry in enumerate(order)
        ]
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_RANDOM,
            victim_order=victims,
            seed_used=seed,
        )
