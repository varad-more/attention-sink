"""Domain-level failures.

These are raised by pure code and carry no transport or AWS semantics. Every one
of them signals a violated experimental invariant, so callers must surface them
rather than degrade: a silently repaired invariant invalidates the run.
"""

__all__ = [
    "BudgetInfeasibleError",
    "DomainError",
    "LineageError",
    "PolicyError",
]


class DomainError(Exception):
    """Base class for violations of an experimental invariant."""


class BudgetInfeasibleError(DomainError):
    """The active-memory budget cannot be met without breaking a stronger rule.

    Raised when the memories a policy is forbidden to evict (the current cycle's
    admission, or an arm's pinned origin) already exceed the token budget. This is
    a protocol misconfiguration, not a runtime condition to be smoothed over.
    """


class PolicyError(DomainError):
    """A rebalance plan is internally inconsistent with the state it was built for."""


class LineageError(DomainError):
    """A summary record does not preserve a resolvable link to its source memories."""
