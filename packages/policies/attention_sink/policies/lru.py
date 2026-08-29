"""Least-recently-cited eviction: the arm that keeps what it last leaned on."""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.memory import Memory
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import MEMORY_ID_FIELD, Ordering, ordered_decision

__all__ = ["LRU_ORDERING", "LeastRecentlyCitedPolicy"]

_NEVER_CITED = -1
"""Sort sentinel for a memory with no verified citation.

Below every real cycle index, so never-cited memories sort ahead of cited ones
without a branch in the comparison. It is never stored on a memory.
"""


def _has_been_cited(memory: Memory) -> int:
    return 0 if memory.last_verified_citation_cycle is None else 1


def _last_cited(memory: Memory) -> int:
    cycle = memory.last_verified_citation_cycle
    return _NEVER_CITED if cycle is None else cycle


LRU_ORDERING: Ordering = (
    ("never_cited", _has_been_cited),
    ("last_verified_citation_cycle", _last_cited),
    ("birth_cycle", lambda memory: memory.birth_cycle),
    MEMORY_ID_FIELD,
)
"""Never-cited first, then least recently cited, then oldest, then identifier.

Never-cited memories lead explicitly rather than by giving them cycle ``-1``,
because "has never been used" and "was last used before the run began" are
different facts and the recorded rank key should say which one applied.
"""


@dataclass(frozen=True, slots=True)
class LeastRecentlyCitedPolicy:
    """Evicts the memory whose last verified citation is furthest in the past.

    Recency is measured from *audited* citations only, and only from the writer's:
    an interview or an evaluation that touches a memory leaves this arm's state
    exactly as it found it. Otherwise probing the arm would change what it goes on
    to remember, and the measurement would no longer be of the mechanism.
    """

    arm_id: ArmId = ArmId.ARM_LRU
    policy_version: str = "lru-v1"

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Retire memories in ascending recency of verified citation."""
        return ordered_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.EVICTED_LEAST_RECENTLY_CITED,
            ordering=LRU_ORDERING,
        )
