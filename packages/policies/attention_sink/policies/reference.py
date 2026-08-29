"""Reference arms that bound the result, rather than competing in it.

Neither arm is part of the canonical six. They exist so that a difference measured
between mechanisms can be read against the best and worst cases available: perfect
recall on one side, none at all on the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId, DecisionCode
from attention_sink.domain.errors import BudgetInfeasibleError
from attention_sink.domain.rebalance import (
    RebalanceContext,
    RebalanceDecision,
    RebalancePlan,
)
from attention_sink.policies.base import evictable_entries, plan_from_victim_order

__all__ = ["FullMemoryPolicy", "StatelessPolicy"]


@dataclass(frozen=True, slots=True)
class FullMemoryPolicy:
    """Evicts nothing: the upper reference, an agent that forgets nothing.

    This arm must be configured with a budget large enough to hold the entire run.
    If it is not, planning raises rather than quietly evicting, because a
    full-memory reference that silently forgot would invalidate every comparison
    drawn against it.
    """

    arm_id: ArmId = ArmId.ARM_FULL
    policy_version: str = "full-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Retain everything, and fail loudly if the configured budget cannot."""
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_OLDEST,
            victim_order=(),
        )


@dataclass(frozen=True, slots=True)
class StatelessPolicy:
    """Retains only the current cycle's admission: the lower reference, no past.

    Every past memory is dropped whether or not the budget required it, so this arm
    measures what the stimulus alone can produce.
    """

    arm_id: ArmId = ArmId.ARM_STATELESS
    policy_version: str = "stateless-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice every memory not admitted in this cycle."""
        evictable = evictable_entries(candidate, context)
        dropped = {entry.memory_id for entry in evictable}
        kept = tuple(entry for entry in candidate.entries if entry.memory_id not in dropped)
        kept_tokens = sum(entry.token_count for entry in kept)
        if kept_tokens > candidate.budget_tokens:
            msg = (
                f"{context.arm_id.value} cycle {context.cycle_index}: this cycle's "
                f"admission alone costs {kept_tokens} tokens, over the budget of "
                f"{candidate.budget_tokens}"
            )
            raise BudgetInfeasibleError(msg)
        return RebalancePlan(
            run_id=context.run_id,
            arm_id=context.arm_id,
            cycle_index=context.cycle_index,
            policy_version=self.policy_version,
            retained_memory_ids=tuple(entry.memory_id for entry in kept),
            decisions=tuple(
                RebalanceDecision(
                    memory_id=entry.memory_id,
                    code=DecisionCode.EVICTED_STATELESS,
                    rank_key=f"origin_ordinal={entry.origin_ordinal}",
                )
                for entry in evictable
            ),
            projected_tokens=kept_tokens,
        )
