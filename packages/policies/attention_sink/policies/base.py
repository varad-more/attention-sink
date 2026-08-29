"""Shared machinery for order-driven eviction policies.

Five of the six canonical policies differ only in *which order* they would sacrifice
memories in. Factoring that ordering out means the budget arithmetic, the protection
of the current cycle's admission, and the infeasibility check are provably identical
across arms -- so any divergence observed between arms is attributable to the
ordering alone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from attention_sink.domain.active_memory import ActiveMemory, ActiveMemoryEntry
from attention_sink.domain.enums import DecisionCode
from attention_sink.domain.errors import BudgetInfeasibleError
from attention_sink.domain.rebalance import RebalanceContext, RebalanceDecision, RebalancePlan

__all__ = ["Victim", "evictable_entries", "plan_from_victim_order"]

Victim = tuple[ActiveMemoryEntry, str]
"""An eviction candidate paired with the rendering of the key that ranked it."""


def evictable_entries(
    candidate: ActiveMemory,
    context: RebalanceContext,
    protect: Callable[[ActiveMemoryEntry], bool] | None = None,
) -> tuple[ActiveMemoryEntry, ...]:
    """Return the entries a policy is permitted to evict, in origin order.

    The memory admitted in the current cycle is always protected. Every arm applies
    that rule, so the experience an agent has just had is never the thing it forgets
    in the same breath -- and no arm gains an advantage from the exception.
    """
    return tuple(
        entry
        for entry in candidate.entries
        if entry.admitted_cycle != context.cycle_index and not (protect and protect(entry))
    )


def plan_from_victim_order(
    candidate: ActiveMemory,
    context: RebalanceContext,
    *,
    policy_version: str,
    code: DecisionCode,
    victim_order: Sequence[Victim],
    seed_used: int | None = None,
) -> RebalancePlan:
    """Evict from ``victim_order`` until the arm is within budget, and stop there.

    Args:
        candidate: Active memory after this cycle's admissions.
        context: The run, arm, and cycle being planned.
        policy_version: Recorded on the plan for later reinterpretation.
        code: The decision code attributed to every eviction this policy makes.
        victim_order: Sacrifice order, most expendable first. Must contain only
            evictable entries and must be a total order (no ties left unresolved).
        seed_used: The derived seed, for policies whose order is pseudo-random.

    Returns:
        A plan retaining as many memories as the budget allows.

    Raises:
        BudgetInfeasibleError: Evicting every candidate still leaves the arm over
            budget, meaning the protected memories alone exceed it.
    """
    remaining = candidate.total_tokens
    budget = candidate.budget_tokens
    decisions: list[RebalanceDecision] = []
    evicted: set[str] = set()

    for entry, rank_key in victim_order:
        if remaining <= budget:
            break
        remaining -= entry.token_count
        evicted.add(entry.memory_id)
        decisions.append(RebalanceDecision(memory_id=entry.memory_id, code=code, rank_key=rank_key))

    if remaining > budget:
        msg = (
            f"{context.arm_id.value} cycle {context.cycle_index}: protected memories "
            f"cost {remaining} tokens, over the budget of {budget}"
        )
        raise BudgetInfeasibleError(msg)

    return RebalancePlan(
        run_id=context.run_id,
        arm_id=context.arm_id,
        cycle_index=context.cycle_index,
        policy_version=policy_version,
        retained_memory_ids=tuple(mid for mid in candidate.memory_ids if mid not in evicted),
        decisions=tuple(decisions),
        seed_used=seed_used,
        projected_tokens=remaining,
    )
