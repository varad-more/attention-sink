"""The contract every memory-rebalance mechanism implements.

Declared in the domain so that policy implementations, AWS adapters, and tests all
depend on the same abstraction and none of them depend on each other.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from attention_sink.domain.cycle import CycleContext
from attention_sink.domain.decision import CompressionPlan, PolicyDecision
from attention_sink.domain.enums import ArmId
from attention_sink.domain.memory import Memory
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import TokenBudget

__all__ = ["CompressingMemoryPolicy", "MemoryPolicy"]


@runtime_checkable
class MemoryPolicy(Protocol):
    """Decides which memories survive a cycle, deterministically and in isolation.

    Implementations must be pure functions of ``(state, budget, context)``: no clock,
    no network, no unseeded randomness, and no knowledge of any other arm. A policy
    that needs randomness derives it from ``context.run_random_seed`` and records the
    derivation on the decision.

    Every implementation also honours the invariants that make the arms comparable,
    so that they differ *only* in mechanism:

    * A memory born in the current cycle is never retired in that cycle.
    * A pinned memory is never retired.
    * Ties are broken by a canonical ordered tuple ending in ``memory_id``, which is
      unique per arm and therefore yields a total order.
    * The input state is never mutated.
    """

    @property
    def arm_id(self) -> ArmId:
        """The arm this policy governs."""

    @property
    def policy_version(self) -> str:
        """Version recorded on every decision, bumped whenever behaviour changes."""

    def rebalance(
        self,
        state: MemoryState,
        budget: TokenBudget,
        context: CycleContext,
    ) -> PolicyDecision:
        """Decide the rebalance for one arm-cycle.

        Args:
            state: The arm's memory *after* this cycle's admissions. Its active set
                may exceed the budget; resolving that is the policy's job.
            context: The run, arm, cycle, and seed the decision is made under.
            budget: The active-memory ceiling and the counter that measures it.

        Returns:
            A decision that is either final and within budget, or requests a
            compression that will get there.

        Raises:
            UnsatisfiableBudgetError: The memories this policy may not retire
                already exceed the budget.
        """


@runtime_checkable
class CompressingMemoryPolicy(MemoryPolicy, Protocol):
    """A policy whose decision may depend on text a model has not written yet.

    Two-stage by necessity rather than by preference: the policy must choose what to
    compress before anything can summarise it, and must charge the result against
    the budget after. Nothing in the policy package calls a model; the caller does,
    between the two stages.
    """

    def finalize_compression(
        self,
        state: MemoryState,
        budget: TokenBudget,
        context: CycleContext,
        plan: CompressionPlan,
        summary: Memory,
    ) -> PolicyDecision:
        """Commit a summary produced for ``plan`` and decide what remains.

        Args:
            state: The same state the plan was produced from.
            budget: The active-memory ceiling.
            context: The run, arm, and cycle being decided.
            plan: The plan the summary was written for.
            summary: The schema-validated summary memory.

        Returns:
            A decision that commits the compression, and either completes the cycle
            or requests one further compression.

        Raises:
            LineageError: The summary does not name exactly the planned sources.
            UnsatisfiableBudgetError: No legal decision reaches the budget.
        """
