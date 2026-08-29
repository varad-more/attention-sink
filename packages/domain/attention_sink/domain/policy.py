"""The contract every memory-rebalance mechanism implements.

Declared in the domain so that policy implementations, AWS adapters, and tests all
depend on the same abstraction and none of them depend on each other.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from attention_sink.domain.active_memory import ActiveMemory
from attention_sink.domain.enums import ArmId
from attention_sink.domain.rebalance import RebalanceContext, RebalancePlan

__all__ = ["RebalancePolicy"]


@runtime_checkable
class RebalancePolicy(Protocol):
    """Decides which memories survive a cycle, deterministically and in isolation.

    Implementations must be pure functions of ``(candidate, context)``: no clock, no
    network, no unseeded randomness, and no knowledge of any other arm. A policy
    that needs randomness derives it from ``context.run_seed`` and records the
    derived seed on the plan.

    Every implementation must also honour two shared invariants, so that the arms
    differ *only* in mechanism:

    * A memory admitted in the current cycle is never evicted in that cycle.
    * Ties are broken by ascending ``origin_ordinal``, which is unique per arm and
      therefore yields a total order.
    """

    @property
    def arm_id(self) -> ArmId:
        """The arm this policy governs."""

    @property
    def policy_version(self) -> str:
        """Version recorded on every plan, bumped whenever behaviour changes."""

    def plan(self, candidate: ActiveMemory, context: RebalanceContext) -> RebalancePlan:
        """Decide the rebalance for one arm-cycle.

        Args:
            candidate: Active memory *after* this cycle's admissions. It may exceed
                the arm's budget; resolving that is the policy's job.
            context: The run, arm, cycle, and seed the decision is made under.

        Returns:
            A plan whose ``projected_tokens`` are within ``candidate.budget_tokens``.

        Raises:
            BudgetInfeasibleError: The memories this policy may not evict already
                exceed the budget.
        """
