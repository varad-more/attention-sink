"""Citation-weighted retention: the arm that keeps what it has actually used."""

from __future__ import annotations

from dataclasses import dataclass, field

from attention_sink.domain.configuration import HeavyHitterConfig
from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import PolicyDecision
from attention_sink.domain.enums import ArmId, PolicyDecisionCode
from attention_sink.domain.memory import Memory
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import (
    MEMORY_ID_FIELD,
    Ordering,
    build_decision,
    eligible_memories,
    greedy_victims,
    rank_candidates,
)

__all__ = ["CitationWeightPolicy"]

_NEVER_CITED = -1


def _last_cited(memory: Memory) -> int:
    cycle = memory.last_verified_citation_cycle
    return _NEVER_CITED if cycle is None else cycle


def _ordering(reserved: frozenset[str]) -> Ordering:
    """Build the eviction order for one cycle, given this cycle's recency reserve.

    ``recency_reserved`` leads the tuple, so reserved memories sort behind every
    unreserved one and are reached only after the unreserved pool is exhausted. That
    makes "break the reserve, but only as far as the budget demands" fall out of the
    ordering itself rather than needing a second pass with different rules.
    """
    return (
        ("recency_reserved", lambda memory: int(memory.memory_id in reserved)),
        ("retention_density", lambda memory: memory.retention_density),
        ("discounted_citation_score", lambda memory: memory.discounted_citation_score),
        ("last_verified_citation_cycle", _last_cited),
        ("birth_cycle", lambda memory: memory.birth_cycle),
        MEMORY_ID_FIELD,
    )


@dataclass(frozen=True, slots=True)
class CitationWeightPolicy:
    """Retains the heavy hitters: the memories the writer keeps returning to.

    Weight is the discounted count of verified writer citations, updated for every
    active memory each cycle as ``new = decay * previous + citations_this_cycle``.
    Eviction minimises *retention density* -- weight per budget token -- so a memory
    earns its space rather than merely its keep, and a long memory has to earn more
    of it. Contrasting this arm with the recency arm is how the experiment separates
    "used a lot" from "used lately".

    The newest few active memories are held in a recency reserve. Without it the arm
    would evict every new memory on sight: a memory that has not yet been shown to
    the writer scores zero by construction, not because it turned out to be
    worthless. When the budget cannot be reached without invading that reserve, the
    reserve is invaded in the same deterministic order and the decision says so.
    """

    arm_id: ArmId = ArmId.ARM_HEAVY
    policy_version: str = "heavy-v1"
    config: HeavyHitterConfig = field(default_factory=HeavyHitterConfig)

    def _reserved_ids(self, state: MemoryState) -> frozenset[str]:
        reserve = self.config.recency_reserve
        if reserve <= 0:
            return frozenset()
        newest = sorted(
            state.active_memories, key=lambda memory: memory.creation_sequence, reverse=True
        )
        return frozenset(memory.memory_id for memory in newest[:reserve])

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Retire the least densely cited memories, invading the reserve only if forced."""
        reserved = self._reserved_ids(state)
        candidates = rank_candidates(eligible_memories(state, context), _ordering(reserved))
        victims, tokens_after = greedy_victims(
            candidates, tokens_before=state.active_tokens, budget=budget
        )
        if not victims:
            code = PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET
        elif any(memory.memory_id in reserved for memory in victims):
            code = PolicyDecisionCode.HEAVY_HITTER_RESERVE_BROKEN
        else:
            code = PolicyDecisionCode.EVICTED_LOWEST_RETENTION_DENSITY
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=code,
            candidates=candidates,
            victims=victims,
            tokens_after=tokens_after,
        )
