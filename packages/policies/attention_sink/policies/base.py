"""Shared machinery for order-driven eviction.

Every canonical arm except the summarising one differs from the others in exactly
one respect: the order in which it would sacrifice memories. Factoring everything
else out -- eligibility, the budget arithmetic, the infeasibility check, the shape of
the recorded decision -- means those parts are provably identical across arms, so any
divergence observed between arms is attributable to the ordering alone.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import (
    CandidateRank,
    CompressionPlan,
    MemoryRetirement,
    PolicyDecision,
    RandomProvenance,
)
from attention_sink.domain.enums import MemoryStatus, PolicyDecisionCode
from attention_sink.domain.errors import UnsatisfiableBudgetError
from attention_sink.domain.explain import render_explanation
from attention_sink.domain.memory import Memory, MemoryLineageEdge
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget

__all__ = [
    "MEMORY_ID_FIELD",
    "Candidate",
    "Ordering",
    "build_decision",
    "eligible_memories",
    "greedy_victims",
    "ordered_decision",
    "rank_candidates",
]

RankField = tuple[str, Callable[[Memory], Any]]
Ordering = tuple[RankField, ...]
"""A total order over memories, as named fields evaluated in sequence.

Declaring the ordering as data rather than as a bare ``key=`` lambda means the same
definition produces both the sort and the human-readable rank key, so the provenance
a reader sees cannot drift away from the comparison that actually ran.
"""

Candidate = tuple[Memory, str]
"""An eligible memory paired with the rendering of the key that ranked it."""

MEMORY_ID_FIELD: RankField = ("memory_id", lambda memory: memory.memory_id)
"""The mandatory final field of every ordering.

Memory identifiers are unique within an arm, so appending this makes any ordering
total: two memories can never compare equal, and no arm's decisions depend on the
order its inputs happened to arrive in.
"""


def _render(value: Any) -> str:
    return repr(value) if isinstance(value, float) else str(value)


def eligible_memories(
    state: MemoryState,
    context: CycleContext,
    *,
    protect: Callable[[Memory], bool] | None = None,
) -> tuple[Memory, ...]:
    """Return the active memories this policy is permitted to retire.

    Two exclusions apply to every arm, which is what keeps them comparable: a memory
    born in the current cycle is never retired in that cycle, and a pinned memory is
    never retired at all. An arm that could forget the experience it has just had
    would be running a different protocol from the others.
    """
    return tuple(
        memory
        for memory in state.active_memories
        if memory.birth_cycle != context.cycle
        and not memory.pinned
        and not (protect is not None and protect(memory))
    )


def rank_candidates(memories: Sequence[Memory], ordering: Ordering) -> tuple[Candidate, ...]:
    """Sort ``memories`` by ``ordering``, most expendable first, and render the keys.

    Raises:
        ValueError: The ordering does not end in ``memory_id`` and so is not total.
    """
    if not ordering or ordering[-1][0] != MEMORY_ID_FIELD[0]:
        msg = "an ordering must end in memory_id to be total"
        raise ValueError(msg)
    ranked = sorted(memories, key=lambda memory: tuple(fn(memory) for _, fn in ordering))
    return tuple(
        (memory, ";".join(f"{name}={_render(fn(memory))}" for name, fn in ordering))
        for memory in ranked
    )


def greedy_victims(
    candidates: Sequence[Candidate],
    *,
    tokens_before: int,
    budget: TokenBudget,
) -> tuple[tuple[Memory, ...], int]:
    """Take the shortest prefix of ``candidates`` that brings the arm within budget.

    Greedy is not an approximation here. The ordering already encodes the policy's
    entire notion of what is expendable, so retiring anything other than the next
    memory in that order would be a different mechanism, not a better solution to
    the same one.

    Returns:
        The memories to retire, and the tokens remaining once they are gone. The
        caller checks whether that total is legal.
    """
    remaining = tokens_before
    victims: list[Memory] = []
    for memory, _ in candidates:
        if budget.is_satisfied_by(remaining):
            break
        remaining -= memory.token_count
        victims.append(memory)
    return tuple(victims), remaining


def build_decision(
    *,
    state: MemoryState,
    budget: TokenBudget,
    context: CycleContext,
    policy_version: str,
    code: PolicyDecisionCode,
    candidates: Sequence[Candidate],
    victims: Sequence[Memory],
    tokens_after: int,
    retirement_status: MemoryStatus = MemoryStatus.EVICTED,
    compressed_memories: Sequence[Memory] = (),
    created_memories: Sequence[Memory] = (),
    lineage_edges: Sequence[MemoryLineageEdge] = (),
    random_provenance: RandomProvenance | None = None,
    compression_plan: CompressionPlan | None = None,
    committed_compression: CompressionPlan | None = None,
    summary_tokens: int = 0,
) -> PolicyDecision:
    """Assemble the recorded decision, checking it reaches a legal budget.

    Memories in ``compressed_memories`` are retired as
    :attr:`~attention_sink.domain.enums.MemoryStatus.COMPRESSED` and the rest as
    ``retirement_status``, so a single decision can both fold memories into a summary
    and evict others when the summary alone did not reach the budget.

    Raises:
        UnsatisfiableBudgetError: ``tokens_after`` still exceeds the budget with
            every eligible memory already retired, which means the memories this
            policy may not touch exceed the budget on their own.
    """
    is_final = compression_plan is None
    if is_final and not budget.is_satisfied_by(tokens_after):
        protected = tokens_after
        msg = (
            f"retiring every eligible memory still leaves {protected} tokens active, "
            f"over the budget of {budget.max_active_tokens}; the memories this arm may "
            f"not retire exceed the budget on their own"
        )
        raise UnsatisfiableBudgetError(
            msg,
            run_id=context.run_id,
            arm_id=context.arm_id.value,
            cycle=context.cycle,
            policy_version=policy_version,
        )

    compressed_ids = tuple(memory.memory_id for memory in compressed_memories)
    evicted_ids = tuple(memory.memory_id for memory in victims)
    retired_ids = compressed_ids + evicted_ids
    dropped = set(retired_ids)
    kept_ids = tuple(
        memory_id for memory_id in state.active_memory_ids if memory_id not in dropped
    ) + tuple(memory.memory_id for memory in created_memories)

    described = compression_plan if compression_plan is not None else committed_compression
    explanation = render_explanation(
        arm_id=context.arm_id,
        cycle=context.cycle,
        code=code,
        budget_tokens=budget.max_active_tokens,
        tokens_before=state.active_tokens,
        tokens_after=tokens_after,
        kept_memory_ids=kept_ids,
        retired_memory_ids=retired_ids,
        eligible_count=len(candidates),
        compression_sources=len(described.source_memory_ids) if described else 0,
        summary_limit=described.summary_target_token_limit if described else 0,
        summary_tokens=summary_tokens,
        tokens_freed=described.tokens_freed if described else 0,
    )

    return PolicyDecision(
        run_id=context.run_id,
        arm_id=context.arm_id,
        cycle=context.cycle,
        policy_version=policy_version,
        decision_code=code,
        budget_tokens=budget.max_active_tokens,
        tokens_before=state.active_tokens,
        tokens_after=tokens_after,
        kept_memory_ids=kept_ids,
        retired_memory_ids=retired_ids,
        retirements=tuple(
            MemoryRetirement(memory_id=memory_id, status=MemoryStatus.COMPRESSED, reason=code)
            for memory_id in compressed_ids
        )
        + tuple(
            MemoryRetirement(memory_id=memory_id, status=retirement_status, reason=code)
            for memory_id in evicted_ids
        ),
        created_memories=tuple(created_memories),
        lineage_edges=tuple(lineage_edges),
        candidate_order=tuple(
            CandidateRank(memory_id=memory.memory_id, rank_index=index, rank_key=rank_key)
            for index, (memory, rank_key) in enumerate(candidates)
        ),
        random_provenance=random_provenance,
        compression_plan=compression_plan,
        committed_compression=committed_compression,
        explanation=explanation,
    )


def ordered_decision(
    *,
    state: MemoryState,
    budget: TokenBudget,
    context: CycleContext,
    policy_version: str,
    code: PolicyDecisionCode,
    ordering: Ordering,
    protect: Callable[[Memory], bool] | None = None,
) -> PolicyDecision:
    """Run one complete order-driven rebalance.

    The whole of an order-driven arm, given its ordering: decide what is eligible,
    rank it, retire the shortest prefix that reaches the budget, and record the
    result. An arm that does nothing because it already fitted the budget says so
    with its own code rather than reporting an eviction of zero memories.
    """
    candidates = rank_candidates(eligible_memories(state, context, protect=protect), ordering)
    victims, tokens_after = greedy_victims(
        candidates, tokens_before=state.active_tokens, budget=budget
    )
    return build_decision(
        state=state,
        budget=budget,
        context=context,
        policy_version=policy_version,
        code=code if victims else PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET,
        candidates=candidates,
        victims=victims,
        tokens_after=tokens_after,
    )
