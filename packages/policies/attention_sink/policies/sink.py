"""Pinned-origin policy: an immovable founding memory plus a sliding window."""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.active_memory import ActiveMemory, ActiveMemoryEntry
from attention_sink.domain.enums import ArmId, DecisionCode, MemoryKind
from attention_sink.domain.rebalance import RebalanceContext, RebalancePlan
from attention_sink.policies.base import Victim, evictable_entries, plan_from_victim_order

__all__ = ["PinnedOriginPolicy"]


def _is_seed(entry: ActiveMemoryEntry) -> bool:
    return entry.record.kind is MemoryKind.SEED


@dataclass(frozen=True, slots=True)
class PinnedOriginPolicy:
    """Never evicts a seed memory; slides a first-in-first-out window over the rest.

    The seed set is the one thing every arm starts from, so pinning it isolates a
    single question: does permanently anchoring an identity change what an agent
    becomes, when the working window is otherwise the same as the plain oldest-first
    arm? Because seed memories are pinned, this arm's usable window is strictly
    smaller than that arm's -- that cost is the point, not a flaw.
    """

    arm_id: ArmId = ArmId.ARM_SINK
    policy_version: str = "sink-v1"

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Sacrifice non-seed memories in ascending insertion order."""
        victims: list[Victim] = [
            (entry, f"pinned=false;origin_ordinal={entry.origin_ordinal}")
            for entry in evictable_entries(candidate, context, protect=_is_seed)
        ]
        return plan_from_victim_order(
            candidate,
            context,
            policy_version=self.policy_version,
            code=DecisionCode.EVICTED_OUTSIDE_WINDOW,
            victim_order=victims,
        )
