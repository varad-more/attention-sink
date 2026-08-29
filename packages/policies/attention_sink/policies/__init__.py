"""Deterministic memory-rebalance mechanisms, one per experimental arm.

Independently testable by construction: this package depends only on
``attention_sink.domain`` and the standard library. Nothing here performs I/O, reads
a clock, calls a model, or knows that AWS exists.
"""

from attention_sink.policies.fifo import FifoPolicy
from attention_sink.policies.heavy_hitter import CitationWeightPolicy
from attention_sink.policies.lru import LeastRecentlyCitedPolicy
from attention_sink.policies.pinned_origin import PinnedOriginPolicy
from attention_sink.policies.reference import FullMemoryPolicy, StatelessPolicy
from attention_sink.policies.registry import (
    DEFAULT_POLICIES,
    canonical_policies,
    policies_for,
    policy_for,
)
from attention_sink.policies.seeded_random import SeededRandomPolicy
from attention_sink.policies.summarization import SummarizationPolicy

__all__ = [
    "DEFAULT_POLICIES",
    "CitationWeightPolicy",
    "FifoPolicy",
    "FullMemoryPolicy",
    "LeastRecentlyCitedPolicy",
    "PinnedOriginPolicy",
    "SeededRandomPolicy",
    "StatelessPolicy",
    "SummarizationPolicy",
    "canonical_policies",
    "policies_for",
    "policy_for",
]
