"""Closed vocabularies shared by every layer of the experiment.

Every value here is persisted verbatim into the immutable event log, so a value
may be *added* over the life of a run but must never be renamed or removed:
replaying an old cycle has to resolve to the same symbol it was written with.
"""

from enum import StrEnum

__all__ = [
    "CANONICAL_ARMS",
    "REFERENCE_ARMS",
    "ArmId",
    "CycleStatus",
    "DecisionCode",
    "MemoryKind",
]


class ArmId(StrEnum):
    """Neutral internal identifier for an experimental arm.

    Public-facing display names are a presentation concern and must never reach a
    generation, audit, summarisation, or evaluation prompt: the writer model may not
    know which memory policy governs it.
    """

    ARM_FIFO = "arm_fifo"
    ARM_LRU = "arm_lru"
    ARM_HEAVY = "arm_heavy"
    ARM_SINK = "arm_sink"
    ARM_RANDOM = "arm_random"
    ARM_SUMMARY = "arm_summary"
    ARM_FULL = "arm_full"
    ARM_STATELESS = "arm_stateless"


CANONICAL_ARMS: tuple[ArmId, ...] = (
    ArmId.ARM_FIFO,
    ArmId.ARM_LRU,
    ArmId.ARM_HEAVY,
    ArmId.ARM_SINK,
    ArmId.ARM_RANDOM,
    ArmId.ARM_SUMMARY,
)
"""The six arms that constitute the canonical experiment.

They share seed memories, stimuli, writer model, prompts, inference settings, and
active-memory token budget; they differ only in rebalance policy.
"""

REFERENCE_ARMS: tuple[ArmId, ...] = (ArmId.ARM_FULL, ArmId.ARM_STATELESS)
"""Optional upper/lower reference arms. Excluded from canonical comparisons."""


class MemoryKind(StrEnum):
    """Provenance class of an active-memory record."""

    SEED = "seed"
    """Supplied by the seed world before cycle 0. Identical across all arms."""

    THOUGHT = "thought"
    """Writer output admitted at the end of the cycle that produced it."""

    SUMMARY = "summary"
    """Lossy compression of one or more source memories. Always carries lineage."""


class DecisionCode(StrEnum):
    """Why the policy engine reached a particular verdict for one memory.

    Decisions are recorded only for memories that *leave* the active set: survival
    is fully explained by the plan's retained set plus the policy version, so
    emitting a verdict per surviving memory every cycle would add log volume without
    adding provenance. Every code here is produced by deterministic code, never by a
    model.
    """

    EVICTED_OLDEST = "evicted_oldest"
    EVICTED_LEAST_RECENTLY_CITED = "evicted_least_recently_cited"
    EVICTED_LOWEST_CITATION_WEIGHT = "evicted_lowest_citation_weight"
    EVICTED_OUTSIDE_WINDOW = "evicted_outside_window"
    EVICTED_RANDOM = "evicted_random"
    EVICTED_STATELESS = "evicted_stateless"
    COMPRESSED = "compressed"


class CycleStatus(StrEnum):
    """Lifecycle of a canonical cycle. Only ``COMMITTED`` cycles are immutable."""

    PENDING = "pending"
    RUNNING = "running"
    COMMITTED = "committed"
    FAILED = "failed"
