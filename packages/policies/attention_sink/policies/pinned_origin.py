"""Pinned origin plus a sliding window: an immovable first day, and then FIFO."""

from __future__ import annotations

from dataclasses import dataclass, field

from attention_sink.domain.configuration import PinnedOriginConfig
from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.memory import Memory
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import ordered_decision
from attention_sink.policies.fifo import FIFO_ORDERING

__all__ = ["PinnedOriginPolicy"]


@dataclass(frozen=True, slots=True)
class PinnedOriginPolicy:
    """Never retires the configured origin memory; slides a FIFO window over the rest.

    Pinning the one memory every arm starts from isolates a single question: does
    permanently anchoring an origin change what an agent becomes, when the working
    window is otherwise identical to the plain oldest-first arm? Because the pinned
    memory holds its tokens forever, this arm's usable window is strictly smaller
    than that arm's. That cost is the mechanism, not a flaw in it.

    The pin is enforced twice over. Every arm refuses to retire a memory flagged
    ``pinned``, and this arm additionally protects the identifier its configuration
    names, so a run can pin its origin by flagging the seed record, by naming it in
    configuration, or both.
    """

    arm_id: ArmId = ArmId.ARM_SINK
    policy_version: str = "sink-v1"
    config: PinnedOriginConfig = field(default_factory=PinnedOriginConfig)

    def _is_pinned(self, memory: Memory) -> bool:
        return memory.memory_id == self.config.pinned_memory_id

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Retire unpinned memories in ascending age until the arm is within budget."""
        return ordered_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.EVICTED_OUTSIDE_WINDOW,
            ordering=FIFO_ORDERING,
            protect=self._is_pinned,
        )
