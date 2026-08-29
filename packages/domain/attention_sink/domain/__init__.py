"""Pure experiment domain: memory records, active-memory state, and rebalance plans.

This package must never import ``boto3``, Strands, Lambda utilities, or any
frontend code. That boundary is enforced by ``tests/unit/test_import_boundaries.py``
and is what allows the mechanism under study to be tested without AWS.
"""

from attention_sink.domain.active_memory import ActiveMemory, ActiveMemoryEntry
from attention_sink.domain.enums import (
    CANONICAL_ARMS,
    REFERENCE_ARMS,
    ArmId,
    CycleStatus,
    DecisionCode,
    MemoryKind,
)
from attention_sink.domain.errors import (
    BudgetInfeasibleError,
    DomainError,
    LineageError,
    PolicyError,
)
from attention_sink.domain.memory import MemoryRecord, make_memory_id
from attention_sink.domain.policy import RebalancePolicy
from attention_sink.domain.rebalance import (
    CompressionRequest,
    RebalanceContext,
    RebalanceDecision,
    RebalancePlan,
    apply_plan,
    derive_arm_cycle_seed,
)
from attention_sink.domain.tokens import HeuristicTokenCounter, TokenCounter

__all__ = [
    "CANONICAL_ARMS",
    "REFERENCE_ARMS",
    "ActiveMemory",
    "ActiveMemoryEntry",
    "ArmId",
    "BudgetInfeasibleError",
    "CompressionRequest",
    "CycleStatus",
    "DecisionCode",
    "DomainError",
    "HeuristicTokenCounter",
    "LineageError",
    "MemoryKind",
    "MemoryRecord",
    "PolicyError",
    "RebalanceContext",
    "RebalanceDecision",
    "RebalancePlan",
    "RebalancePolicy",
    "TokenCounter",
    "apply_plan",
    "derive_arm_cycle_seed",
    "make_memory_id",
]
