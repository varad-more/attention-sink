"""The pure experiment kernel: memories, state, policy decisions, and lineage.

This package must never import ``boto3``, Strands, Lambda utilities, or any frontend
code. That boundary is enforced by ``tests/unit/test_import_boundaries.py`` and is
what allows the mechanism under study to be tested without AWS, a network, or a
model provider.
"""

from attention_sink.domain.citations import CitationClaim, VerifiedCitation
from attention_sink.domain.configuration import (
    DEFAULT_CITATION_DECAY,
    DEFAULT_RECENCY_RESERVE,
    HeavyHitterConfig,
    InferenceParameters,
    ModelConfiguration,
    PinnedOriginConfig,
    PolicyConfiguration,
    RunConfiguration,
    SummarizationConfig,
)
from attention_sink.domain.cycle import CycleContext, CycleSnapshot
from attention_sink.domain.decision import (
    CandidateRank,
    CompressionPlan,
    MemoryRetirement,
    PolicyDecision,
    RandomDraw,
    RandomProvenance,
)
from attention_sink.domain.enums import (
    CANONICAL_ARMS,
    REFERENCE_ARMS,
    RETIRED_STATUSES,
    ArmId,
    CitationSource,
    CycleStatus,
    LedgerEventType,
    LineageRelation,
    MemoryKind,
    MemoryStatus,
    PolicyDecisionCode,
)
from attention_sink.domain.errors import (
    DomainError,
    LineageError,
    PolicyError,
    StateError,
    UnsatisfiableBudgetError,
)
from attention_sink.domain.explain import render_explanation
from attention_sink.domain.hashing import content_hash, selection_digest, state_hash
from attention_sink.domain.identifiers import (
    CycleNumber,
    EventId,
    MemoryId,
    PromptVersion,
    ProtocolVersion,
    RunId,
    StimulusId,
    UtcTimestamp,
    Version,
)
from attention_sink.domain.ledger import LedgerEvent, MetricEvidence
from attention_sink.domain.memory import (
    MIN_SUMMARY_SOURCES,
    Memory,
    MemoryLineageEdge,
    make_memory_id,
)
from attention_sink.domain.policy import CompressingMemoryPolicy, MemoryPolicy
from attention_sink.domain.state import MemoryState
from attention_sink.domain.tokens import HeuristicTokenCounter, TokenBudget, TokenCounter

__all__ = [
    "CANONICAL_ARMS",
    "DEFAULT_CITATION_DECAY",
    "DEFAULT_RECENCY_RESERVE",
    "MIN_SUMMARY_SOURCES",
    "REFERENCE_ARMS",
    "RETIRED_STATUSES",
    "ArmId",
    "CandidateRank",
    "CitationClaim",
    "CitationSource",
    "CompressingMemoryPolicy",
    "CompressionPlan",
    "CycleContext",
    "CycleNumber",
    "CycleSnapshot",
    "CycleStatus",
    "DomainError",
    "EventId",
    "HeavyHitterConfig",
    "HeuristicTokenCounter",
    "InferenceParameters",
    "LedgerEvent",
    "LedgerEventType",
    "LineageError",
    "LineageRelation",
    "Memory",
    "MemoryId",
    "MemoryKind",
    "MemoryLineageEdge",
    "MemoryPolicy",
    "MemoryRetirement",
    "MemoryState",
    "MemoryStatus",
    "MetricEvidence",
    "ModelConfiguration",
    "PinnedOriginConfig",
    "PolicyConfiguration",
    "PolicyDecision",
    "PolicyDecisionCode",
    "PolicyError",
    "PromptVersion",
    "ProtocolVersion",
    "RandomDraw",
    "RandomProvenance",
    "RunConfiguration",
    "RunId",
    "StateError",
    "StimulusId",
    "SummarizationConfig",
    "TokenBudget",
    "TokenCounter",
    "UnsatisfiableBudgetError",
    "UtcTimestamp",
    "VerifiedCitation",
    "Version",
    "content_hash",
    "make_memory_id",
    "render_explanation",
    "selection_digest",
    "state_hash",
]
