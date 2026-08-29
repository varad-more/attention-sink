"""Order-driven policies keyed on insertion order, recency, and citation weight.

Three mechanisms, one file, because they are only meaningful in contrast with each
other: they share an eviction skeleton and differ solely in the ranking key.
"""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId, DecisionCode
from attention_sink.domain.rebalance import RebalanceContext, RebalancePlan
from attention_sink.policies.base import Victim, evictable_entries, plan_from_victim_order

__all__ = ["CitationWeightPolicy", "FifoPolicy", "LeastRecentlyCitedPolicy"]


@dataclass(frozen=True, slots=True)
class FifoPolicy:
    """Evicts the oldest memory first, regardless of whether it is still in use.

    The null hypothesis of the experiment: a mind with no notion of importance. It
    is the baseline every other mechanism has to beat.
    """

    arm_id: ArmId = ArmId.ARM_FIFO
    policy_version: str = "fifo-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice memories in ascending insertion order."""
        victims: list[Victim] = [
            (entry, f"origin_ordinal={entry.origin_ordinal}")
            for entry in evictable_entries(candidate, context)
        ]
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_OLDEST,
            victim_order=victims,
        )


@dataclass(frozen=True, slots=True)
class LeastRecentlyCitedPolicy:
    """Evicts the memory whose last *verified* citation is furthest in the past.

    Recency is measured from audited citations only. A memory the agent has never
    cited falls back to the cycle it was admitted in, which gives every memory a
    defined position and makes the ordering total once ties break on ordinal.
    """

    arm_id: ArmId = ArmId.ARM_LRU
    policy_version: str = "lru-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice memories in ascending (last used cycle, insertion order)."""
        ranked = sorted(
            evictable_entries(candidate, context),
            key=lambda entry: (entry.last_used_cycle, entry.origin_ordinal),
        )
        victims: list[Victim] = [
            (
                entry,
                f"last_used_cycle={entry.last_used_cycle};origin_ordinal={entry.origin_ordinal}",
            )
            for entry in ranked
        ]
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_LEAST_RECENTLY_CITED,
            victim_order=victims,
        )


@dataclass(frozen=True, slots=True)
class CitationWeightPolicy:
    """Retains the heavy hitters: memories cited most often across the whole run.

    Weight is the undecayed count of verified citations, so a memory that mattered
    intensely long ago outranks one that was glanced at recently. Contrasting this
    with the recency arm is how the experiment separates "used a lot" from "used
    lately".
    """

    arm_id: ArmId = ArmId.ARM_HEAVY
    policy_version: str = "heavy-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice memories in ascending (citation count, insertion order)."""
        ranked = sorted(
            evictable_entries(candidate, context),
            key=lambda entry: (entry.citation_count, entry.origin_ordinal),
        )
        victims: list[Victim] = [
            (
                entry,
                f"citation_count={entry.citation_count};origin_ordinal={entry.origin_ordinal}",
            )
            for entry in ranked
        ]
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_LOWEST_CITATION_WEIGHT,
            victim_order=victims,
        )
