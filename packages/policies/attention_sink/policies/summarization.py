"""Lossy hierarchical summarization: forgetting the words but keeping the shape."""

from __future__ import annotations

from dataclasses import dataclass

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId, DecisionCode
from attention_sink.domain.errors import BudgetInfeasibleError
from attention_sink.domain.memory import make_memory_id
from attention_sink.domain.rebalance import (
    CompressionRequest,
    RebalanceContext,
    RebalanceDecision,
    RebalancePlan,
)
from attention_sink.policies.base import evictable_entries

__all__ = ["SummarizationPolicy"]


def _ceil_div(numerator: int, denominator: int) -> int:
    """Integer ceiling division, used so no float ever enters a budget decision."""
    return -(-numerator // denominator)


@dataclass(frozen=True, slots=True)
class SummarizationPolicy:
    """Replaces the oldest memories with one summary that costs a fraction of them.

    The summary is itself an ordinary memory: it occupies the next origin ordinal,
    it is charged against the same token budget, and on a later cycle it can be
    swept into a summary of summaries. That recursion is what makes the compression
    hierarchical, and the lineage recorded on each summary is what keeps it
    auditable back to the original text.

    The policy chooses the source memories and the ceiling on the summary's size; a
    model is asked only to write within that ceiling. It never decides what is lost.
    """

    arm_id: ArmId = ArmId.ARM_SUMMARY
    policy_version: str = "summary-v1"
    compression_numerator: int = 1
    compression_denominator: int = 4
    min_summary_tokens: int = 16
    """Floor on the summary budget.

    Below roughly this size a summary of several distinct episodes degenerates into
    a label, which would make the arm a slower FIFO rather than a compressor.
    """

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Compress the shortest oldest prefix that brings the arm within budget."""
        deficit = candidate.total_tokens - candidate.budget_tokens
        if deficit <= 0:
            return RebalancePlan(
                run_id=context.run_id,
                arm_id=context.arm_id,
                cycle_index=context.cycle_index,
                policy_version=self.policy_version,
                retained_memory_ids=candidate.memory_ids,
                projected_tokens=candidate.total_tokens,
            )

        sources = evictable_entries(candidate, context)
        freed = 0
        for size, entry in enumerate(sources, start=1):
            freed += entry.token_count
            summary_tokens = max(
                self.min_summary_tokens,
                _ceil_div(freed * self.compression_numerator, self.compression_denominator),
            )
            if freed - summary_tokens >= deficit:
                return self._compression_plan(
                    candidate, context, size=size, freed=freed, summary_tokens=summary_tokens
                )

        msg = (
            f"{context.arm_id.value} cycle {context.cycle_index}: compressing every "
            f"eligible memory frees {freed} tokens, short of the {deficit} needed"
        )
        raise BudgetInfeasibleError(msg)

    def _compression_plan(
        self,
        candidate: ActiveMemory,
        context: RebalanceContext,
        *,
        size: int,
        freed: int,
        summary_tokens: int,
    ) -> RebalancePlan:
        """Build the plan that folds the oldest ``size`` evictable memories into one."""
        sources = evictable_entries(candidate, context)[:size]
        source_ids = tuple(entry.memory_id for entry in sources)
        target_ordinal = candidate.next_origin_ordinal
        summary_id = make_memory_id(context.arm_id, target_ordinal)
        compressed = set(source_ids)
        return RebalancePlan(
            run_id=context.run_id,
            arm_id=context.arm_id,
            cycle_index=context.cycle_index,
            policy_version=self.policy_version,
            retained_memory_ids=(
                *(mid for mid in candidate.memory_ids if mid not in compressed),
                summary_id,
            ),
            decisions=tuple(
                RebalanceDecision(
                    memory_id=entry.memory_id,
                    code=DecisionCode.COMPRESSED,
                    rank_key=f"origin_ordinal={entry.origin_ordinal};into={summary_id}",
                )
                for entry in sources
            ),
            compression=CompressionRequest(
                source_memory_ids=source_ids,
                max_summary_tokens=summary_tokens,
                target_origin_ordinal=target_ordinal,
            ),
            projected_tokens=candidate.total_tokens - freed + summary_tokens,
        )
