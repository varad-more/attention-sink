"""Reference arms that bound the result rather than competing in it.

Neither arm is part of the canonical six. They exist so that a difference measured
between mechanisms can be read against the best and worst cases available: perfect
recall on one side, none at all on the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import build_decision, eligible_memories, rank_candidates
from attention_sink.policies.fifo import FIFO_ORDERING

__all__ = ["FullMemoryPolicy", "StatelessPolicy"]


@dataclass(frozen=True, slots=True)
class FullMemoryPolicy:
    """Retires nothing: the upper reference, an agent that forgets nothing.

    This arm must be configured with a budget large enough to hold the whole run. If
    it is not, it raises rather than quietly evicting: a full-memory reference that
    silently forgot would invalidate every comparison drawn against it.
    """

    arm_id: ArmId = ArmId.ARM_FULL
    policy_version: str = "full-v1"

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Keep everything, and fail loudly if the configured budget cannot."""
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.RETAINED_ALL,
            candidates=(),
            victims=(),
            tokens_after=state.active_tokens,
        )


@dataclass(frozen=True, slots=True)
class StatelessPolicy:
    """Keeps only the current cycle's admission: the lower reference, no past at all.

    Every earlier memory is dropped whether or not the budget required it, so this
    arm measures what the stimulus alone can produce.
    """

    arm_id: ArmId = ArmId.ARM_STATELESS
    policy_version: str = "stateless-v1"

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Retire every memory carried in from an earlier cycle."""
        candidates = rank_candidates(eligible_memories(state, context), FIFO_ORDERING)
        victims = tuple(memory for memory, _ in candidates)
        tokens_after = state.active_tokens - sum(memory.token_count for memory in victims)
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.EVICTED_STATELESS
            if victims
            else PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET,
            candidates=candidates,
            victims=victims,
            tokens_after=tokens_after,
        )
