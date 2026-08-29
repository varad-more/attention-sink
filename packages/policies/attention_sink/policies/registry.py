"""The bound set of mechanisms under test.

Resolution goes through this registry so that no service can invent an arm, and so
that an arm's public display name has no path into policy selection. A caller names
an :class:`ArmId`; nothing else selects a mechanism.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType

from attention_sink.domain.configuration import PolicyConfiguration, RunConfiguration
from attention_sink.domain.enums import CANONICAL_ARMS, REFERENCE_ARMS, ArmId
from attention_sink.domain.policy import MemoryPolicy
from attention_sink.policies.fifo import FifoPolicy
from attention_sink.policies.heavy_hitter import CitationWeightPolicy
from attention_sink.policies.lru import LeastRecentlyCitedPolicy
from attention_sink.policies.pinned_origin import PinnedOriginPolicy
from attention_sink.policies.reference import FullMemoryPolicy, StatelessPolicy
from attention_sink.policies.seeded_random import SeededRandomPolicy
from attention_sink.policies.summarization import SummarizationPolicy

__all__ = [
    "DEFAULT_POLICIES",
    "canonical_policies",
    "policies_for",
    "policy_for",
]


def policies_for(config: PolicyConfiguration) -> Mapping[ArmId, MemoryPolicy]:
    """Build every arm's mechanism from one run's configuration.

    Constructed per run rather than held as module state, because the parameters
    that distinguish two runs -- the recency reserve, the summary ceiling, which
    memory is pinned -- are exactly the ones a policy holds.
    """
    return MappingProxyType(
        {
            ArmId.ARM_FIFO: FifoPolicy(),
            ArmId.ARM_LRU: LeastRecentlyCitedPolicy(),
            ArmId.ARM_HEAVY: CitationWeightPolicy(config=config.heavy_hitter),
            ArmId.ARM_SINK: PinnedOriginPolicy(config=config.pinned_origin),
            ArmId.ARM_RANDOM: SeededRandomPolicy(),
            ArmId.ARM_SUMMARY: SummarizationPolicy(config=config.summarization),
            ArmId.ARM_FULL: FullMemoryPolicy(),
            ArmId.ARM_STATELESS: StatelessPolicy(),
        }
    )


DEFAULT_POLICIES: Mapping[ArmId, MemoryPolicy] = policies_for(PolicyConfiguration())
"""Every mechanism at its default parameters, for tests and the simulator."""


def policy_for(arm_id: ArmId, config: PolicyConfiguration | None = None) -> MemoryPolicy:
    """Return the mechanism governing ``arm_id``.

    Raises:
        KeyError: The arm has no registered mechanism, which is a configuration bug
            rather than a runtime condition.
    """
    registry = DEFAULT_POLICIES if config is None else policies_for(config)
    return registry[arm_id]


def canonical_policies(run: RunConfiguration) -> tuple[MemoryPolicy, ...]:
    """The mechanisms of a run's configured arms, in canonical order.

    Ordered by :data:`CANONICAL_ARMS` and then the reference arms, so that any
    report built by iterating this tuple lists the arms the same way every time.
    """
    registry = policies_for(run.policies)
    ordered = (*CANONICAL_ARMS, *REFERENCE_ARMS)
    return tuple(registry[arm] for arm in ordered if arm in run.arms)
