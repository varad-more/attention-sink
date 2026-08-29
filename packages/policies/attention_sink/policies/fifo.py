"""First-in, first-out eviction: the arm with no notion of importance."""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import MEMORY_ID_FIELD, Ordering, ordered_decision

__all__ = ["FIFO_ORDERING", "FifoPolicy"]

FIFO_ORDERING: Ordering = (
    ("birth_cycle", lambda memory: memory.birth_cycle),
    ("creation_sequence", lambda memory: memory.creation_sequence),
    MEMORY_ID_FIELD,
)
"""Oldest first: birth cycle, then arm-local creation order, then identifier.

``birth_cycle`` alone is not a total order, because several memories can be born in
one cycle. ``creation_sequence`` resolves that, and the identifier makes the order
total even if a later protocol admits memories out of sequence.
"""


@dataclass(frozen=True, slots=True)
class FifoPolicy:
    """Evicts the oldest memory first, regardless of whether it is still in use.

    The null hypothesis of the experiment: a mind whose only criterion is age. It is
    the baseline every other mechanism has to beat, and it is also the fallback the
    summarising arm drops to when no legal compression exists.
    """

    arm_id: ArmId = ArmId.ARM_FIFO
    policy_version: str = "fifo-v1"

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Retire memories in ascending age until the arm is within budget."""
        return ordered_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.EVICTED_OLDEST,
            ordering=FIFO_ORDERING,
        )
