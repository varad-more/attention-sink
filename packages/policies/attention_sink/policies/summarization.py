"""Lossy hierarchical summarisation: losing the words while keeping the shape.

This is the only two-stage policy, and it is two-stage by necessity. The summary's
text does not exist when the decision has to be made, and its token cost is not
known until it does. So the policy decides *what* is compressed and *how large* the
result may be; something outside this package writes the words; and the policy then
charges the result against the same budget as any other memory.

Nothing here calls a model. A policy that could ask a model what to forget would put
the model back inside the mechanism the experiment is trying to isolate.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from attention_sink.domain.configuration import SummarizationConfig
from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import CompressionPlan, PolicyDecision
from attention_sink.domain.enums import ArmId, LineageRelation, MemoryKind, PolicyDecisionCode
from attention_sink.domain.errors import (
    ErrorContext,
    LineageError,
    PolicyError,
    UnsatisfiableBudgetError,
)
from attention_sink.domain.memory import Memory, MemoryLineageEdge, make_memory_id
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget
from attention_sink.policies.base import (
    build_decision,
    eligible_memories,
    greedy_victims,
    ordered_decision,
    rank_candidates,
)
from attention_sink.policies.fifo import FIFO_ORDERING

__all__ = ["SummarizationPolicy"]


@dataclass(frozen=True, slots=True)
class SummarizationPolicy:
    """Replaces the oldest memories with one summary that costs a fraction of them.

    A summary is an ordinary memory: it takes the next creation slot, it is charged
    against the same budget, and on a later cycle it can itself be swept into a
    summary of summaries. That recursion is what makes the compression hierarchical,
    and the lineage recorded on every summary is what keeps it auditable back to the
    original text.

    When no legal compression can reach the budget -- too few eligible sources, or a
    summary ceiling too large to help -- the arm falls back to oldest-first eviction
    and says so in its decision code, rather than silently becoming a slow FIFO.
    """

    arm_id: ArmId = ArmId.ARM_SUMMARY
    policy_version: str = "summary-v1"
    config: SummarizationConfig = field(default_factory=SummarizationConfig)

    # ------------------------------------------------------------ stage A

    def rebalance(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Plan a compression, or fall back to eviction when none is legal.

        Returns a decision that is either already within budget, requests a summary
        that will get it there, or -- when no compression can -- has evicted memories
        oldest-first under :attr:`PolicyDecisionCode.SUMMARY_FALLBACK_FIFO`.
        """
        candidates = rank_candidates(eligible_memories(state, context), FIFO_ORDERING)
        if budget.is_satisfied_by(state.active_tokens):
            return build_decision(
                state=state,
                budget=budget,
                context=context,
                policy_version=self.policy_version,
                code=PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET,
                candidates=candidates,
                victims=(),
                tokens_after=state.active_tokens,
            )

        plan = self._plan_compression(
            sources=[memory for memory, _ in candidates],
            tokens_before=state.active_tokens,
            budget=budget,
            summary_memory_id=state.next_memory_id(),
        )
        if plan is None:
            return self._fifo_fallback(state, budget, context)
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.COMPRESSION_PLANNED,
            candidates=candidates,
            victims=(),
            tokens_after=state.active_tokens,
            compression_plan=plan,
        )

    # ------------------------------------------------------------ stage B

    def finalize_compression(
        self,
        state: MemoryState,
        budget: TokenBudget,
        context: CycleContext,
        plan: CompressionPlan,
        summary: Memory,
    ) -> PolicyDecision:
        """Commit ``summary`` against ``plan`` and decide what the arm still needs.

        The budget is re-checked rather than assumed. A plan produced by
        :meth:`rebalance` under this budget always lands inside it, but a plan can
        also arrive from elsewhere -- replayed from the ledger, or carried into a fork
        whose budget was tightened -- and a policy that trusted a plan it did not just
        make would commit a summary that leaves the arm over budget.

        Raises:
            LineageError: The summary does not name exactly the planned sources, or
                does not occupy the identifier the plan reserved.
            PolicyError: The summary costs more tokens than the plan allowed. That is
                a broken contract on the caller's side, not an infeasible budget: the
                policy asked for a ceiling and was handed something over it.
            UnsatisfiableBudgetError: No further decision can reach the budget.
        """
        error_context = ErrorContext(
            run_id=context.run_id,
            arm_id=context.arm_id.value,
            cycle=context.cycle,
            policy_version=self.policy_version,
        )
        self._validate_summary(state, plan, summary, error_context)

        # Sources are ordered the way the plan chose them -- oldest first -- rather than
        # the order they sit in the active set. The recorded ordering and the recorded
        # retirements have to agree, or the provenance would describe a different
        # decision from the one that ran.
        active = {memory.memory_id: memory for memory in state.active_memories}
        source_candidates = rank_candidates(
            [active[memory_id] for memory_id in plan.source_memory_ids], FIFO_ORDERING
        )
        sources = tuple(memory for memory, _ in source_candidates)
        tokens_after = state.active_tokens - plan.tokens_freed + summary.token_count
        lineage = tuple(
            MemoryLineageEdge(
                parent_memory_id=source.memory_id,
                child_memory_id=summary.memory_id,
                relation=LineageRelation.COMPRESSED_INTO,
                cycle=context.cycle,
            )
            for source in sources
        )

        compressed_ids = set(plan.source_memory_ids)
        surviving = [
            memory
            for memory in eligible_memories(state, context)
            if memory.memory_id not in compressed_ids
        ]
        candidates = rank_candidates(surviving, FIFO_ORDERING)

        if budget.is_satisfied_by(tokens_after):
            return build_decision(
                state=state,
                budget=budget,
                context=context,
                policy_version=self.policy_version,
                code=PolicyDecisionCode.COMPRESSION_COMMITTED,
                candidates=[*source_candidates, *candidates],
                victims=(),
                compressed_memories=sources,
                created_memories=(summary,),
                lineage_edges=lineage,
                tokens_after=tokens_after,
                committed_compression=plan,
                summary_tokens=summary.token_count,
            )

        next_plan = self._plan_compression(
            sources=surviving,
            tokens_before=tokens_after,
            budget=budget,
            summary_memory_id=make_memory_id(context.arm_id, state.next_creation_sequence + 1),
        )
        if next_plan is not None:
            return build_decision(
                state=state,
                budget=budget,
                context=context,
                policy_version=self.policy_version,
                code=PolicyDecisionCode.COMPRESSION_PLANNED,
                candidates=[*source_candidates, *candidates],
                victims=(),
                compressed_memories=sources,
                created_memories=(summary,),
                lineage_edges=lineage,
                tokens_after=tokens_after,
                compression_plan=next_plan,
                committed_compression=plan,
                summary_tokens=summary.token_count,
            )

        if not self.config.fifo_fallback_enabled:
            msg = (
                f"the summary left {tokens_after} tokens active and no further legal "
                f"compression exists; the FIFO fallback is disabled for this run"
            )
            raise UnsatisfiableBudgetError(msg, **error_context)

        victims, final_tokens = greedy_victims(
            candidates, tokens_before=tokens_after, budget=budget
        )
        return build_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.SUMMARY_FALLBACK_FIFO,
            candidates=[*source_candidates, *candidates],
            victims=victims,
            compressed_memories=sources,
            created_memories=(summary,),
            lineage_edges=lineage,
            tokens_after=final_tokens,
            committed_compression=plan,
            summary_tokens=summary.token_count,
        )

    # ------------------------------------------------------------- internals

    def _plan_compression(
        self,
        *,
        sources: Sequence[Memory],
        tokens_before: int,
        budget: TokenBudget,
        summary_memory_id: str,
    ) -> CompressionPlan | None:
        """Return the shortest oldest-first compression that reaches the budget.

        Grows the source set one memory at a time and stops at the first prefix that
        both meets the minimum source count and, once replaced by a summary at the
        configured ceiling, leaves the arm inside the budget with its safety margin
        intact. Returns ``None`` when no prefix qualifies, which is a legitimate
        outcome rather than an error: the caller decides what to do instead.
        """
        limit = self.config.summary_target_token_limit
        ceiling = budget.max_active_tokens - self.config.safety_margin_tokens
        freed = 0
        for size, memory in enumerate(sources, start=1):
            freed += memory.token_count
            if size < self.config.min_sources:
                continue
            if tokens_before - freed + limit <= ceiling:
                return CompressionPlan(
                    source_memory_ids=tuple(m.memory_id for m in sources[:size]),
                    summary_memory_id=summary_memory_id,
                    summary_target_token_limit=limit,
                    tokens_freed=freed,
                    safety_margin_tokens=self.config.safety_margin_tokens,
                )
        return None

    def _fifo_fallback(
        self, state: MemoryState, budget: TokenBudget, context: CycleContext
    ) -> PolicyDecision:
        """Evict oldest-first because no legal compression exists this cycle."""
        if not self.config.fifo_fallback_enabled:
            msg = (
                f"no legal compression reaches the {budget.max_active_tokens}-token budget "
                f"and the FIFO fallback is disabled for this run"
            )
            raise UnsatisfiableBudgetError(
                msg,
                run_id=context.run_id,
                arm_id=context.arm_id.value,
                cycle=context.cycle,
                policy_version=self.policy_version,
            )
        return ordered_decision(
            state=state,
            budget=budget,
            context=context,
            policy_version=self.policy_version,
            code=PolicyDecisionCode.SUMMARY_FALLBACK_FIFO,
            ordering=FIFO_ORDERING,
        )

    def _validate_summary(
        self,
        state: MemoryState,
        plan: CompressionPlan,
        summary: Memory,
        error_context: ErrorContext,
    ) -> None:
        """Reject any summary that is not the one this plan asked for."""
        if summary.memory_kind is not MemoryKind.SUMMARY:
            msg = f"memory {summary.memory_id} is a {summary.memory_kind.value}, not a summary"
            raise LineageError(msg, **error_context)
        if set(summary.parent_memory_ids) != set(plan.source_memory_ids):
            msg = (
                f"summary {summary.memory_id} names parents "
                f"{sorted(summary.parent_memory_ids)} but the plan compressed "
                f"{sorted(plan.source_memory_ids)}"
            )
            raise LineageError(msg, **error_context)
        if summary.memory_id != plan.summary_memory_id:
            msg = (
                f"summary {summary.memory_id} does not occupy the identifier "
                f"{plan.summary_memory_id} the plan reserved"
            )
            raise LineageError(msg, **error_context)
        if summary.token_count > plan.summary_target_token_limit:
            msg = (
                f"summary {summary.memory_id} costs {summary.token_count} tokens, over the "
                f"{plan.summary_target_token_limit} the policy allowed"
            )
            raise PolicyError(msg, **error_context)
        missing = [
            memory_id
            for memory_id in plan.source_memory_ids
            if (found := state.get(memory_id)) is None or not found.is_active
        ]
        if missing:
            msg = f"the plan compresses memories that are not active: {sorted(missing)}"
            raise LineageError(msg, **error_context)
