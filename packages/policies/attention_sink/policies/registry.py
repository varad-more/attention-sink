"""The bound set of mechanisms under test.

Resolution goes through this registry so that no service can invent an arm, and so
that an arm's public display name has no path into policy selection.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from attention_sink.domain.enums import CANONICAL_ARMS, REFERENCE_ARMS, ArmId
from attention_sink.domain.policy import RebalancePolicy
from attention_sink.policies.recency import (
    CitationWeightPolicy,
    FifoPolicy,
    LeastRecentlyCitedPolicy,
)
from attention_sink.policies.reference import FullMemoryPolicy, StatelessPolicy
from attention_sink.policies.seeded_random import SeededRandomPolicy
from attention_sink.policies.sink import PinnedOriginPolicy
from attention_sink.policies.summarization import SummarizationPolicy

__all__ = ["POLICIES", "canonical_policies", "policy_for", "reference_policies"]

POLICIES: Mapping[ArmId, RebalancePolicy] = MappingProxyType(
    {
        ArmId.ARM_FIFO: FifoPolicy(),
        ArmId.ARM_LRU: LeastRecentlyCitedPolicy(),
        ArmId.ARM_HEAVY: CitationWeightPolicy(),
        ArmId.ARM_SINK: PinnedOriginPolicy(),
        ArmId.ARM_RANDOM: SeededRandomPolicy(),
        ArmId.ARM_SUMMARY: SummarizationPolicy(),
        ArmId.ARM_FULL: FullMemoryPolicy(),
        ArmId.ARM_STATELESS: StatelessPolicy(),
    }
)


def policy_for(arm_id: ArmId) -> RebalancePolicy:
    """Return the policy governing ``arm_id``.

    Raises:
        KeyError: The arm has no registered mechanism, which is a configuration bug
            rather than a runtime condition.
    """
    return POLICIES[arm_id]


def canonical_policies() -> tuple[RebalancePolicy, ...]:
    """The six mechanisms that constitute the canonical experiment, in fixed order."""
    return tuple(POLICIES[arm] for arm in CANONICAL_ARMS)


def reference_policies() -> tuple[RebalancePolicy, ...]:
    """The two bounding reference mechanisms, in fixed order."""
    return tuple(POLICIES[arm] for arm in REFERENCE_ARMS)
