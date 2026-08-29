"""Domain-level failures.

These are raised by pure code and carry no transport or AWS semantics. Every one of
them signals a violated experimental invariant, so callers must surface them rather
than degrade: a silently repaired invariant invalidates the run.

Every error carries the run, arm, cycle, and policy version it was raised under.
A stack trace tells you which line failed; only that context tells you which
arm-cycle of which run is now unusable.
"""

from __future__ import annotations

from typing import TypedDict

__all__ = [
    "DomainError",
    "ErrorContext",
    "LineageError",
    "PolicyError",
    "StateError",
    "UnsatisfiableBudgetError",
]


class DomainError(Exception):
    """Base class for violations of an experimental invariant."""

    def __init__(
        self,
        message: str,
        *,
        run_id: str | None = None,
        arm_id: str | None = None,
        cycle: int | None = None,
        policy_version: str | None = None,
    ) -> None:
        """Record what failed and the arm-cycle it failed in.

        Args:
            message: What went wrong, in terms a reviewer of the run can act on.
            run_id: Run the failure belongs to, when known.
            arm_id: Arm the failure belongs to, when known.
            cycle: Cycle index the failure belongs to, when known.
            policy_version: Policy that was executing, when one was.
        """
        self.message = message
        self.run_id = run_id
        self.arm_id = arm_id
        self.cycle = cycle
        self.policy_version = policy_version
        super().__init__(self._render())

    def _render(self) -> str:
        context = {
            "run": self.run_id,
            "arm": self.arm_id,
            "cycle": self.cycle,
            "policy": self.policy_version,
        }
        known = [f"{key}={value}" for key, value in context.items() if value is not None]
        return f"[{' '.join(known)}] {self.message}" if known else self.message

    @property
    def context(self) -> dict[str, str | int | None]:
        """The run, arm, cycle, and policy this failure was raised under."""
        return {
            "run_id": self.run_id,
            "arm_id": self.arm_id,
            "cycle": self.cycle,
            "policy_version": self.policy_version,
        }


class UnsatisfiableBudgetError(DomainError):
    """No legal decision reaches the token budget for this arm-cycle.

    Raised when the memories a policy is forbidden to retire -- the current cycle's
    admission, or a pinned origin -- already exceed the budget on their own. This is
    a protocol misconfiguration, not a runtime condition to be smoothed over: the
    run must be reconfigured rather than allowed to continue over budget.
    """


class PolicyError(DomainError):
    """A decision is internally inconsistent with the state it was built for."""


class LineageError(DomainError):
    """A summary does not preserve a resolvable link to its source memories."""


class StateError(DomainError):
    """Memory state was asked for a transition that would break an invariant."""


class ErrorContext(TypedDict):
    """The four coordinates every policy failure must carry.

    A ``TypedDict`` rather than a loose mapping so that unpacking it into a
    :class:`DomainError` is checked: an error that lost its cycle number would be
    indistinguishable from one that never had it.
    """

    run_id: str
    arm_id: str
    cycle: int
    policy_version: str
