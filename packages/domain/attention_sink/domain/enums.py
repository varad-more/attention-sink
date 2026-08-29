"""Closed vocabularies shared by every layer of the experiment.

Every value here is persisted verbatim into the immutable ledger, so a value may be
*added* over the life of a run but must never be renamed or removed: replaying an
old cycle has to resolve to the same symbol it was written with.
"""

from enum import StrEnum

__all__ = [
    "CANONICAL_ARMS",
    "REFERENCE_ARMS",
    "RETIRED_STATUSES",
    "ArmId",
    "CitationSource",
    "CycleStatus",
    "LedgerEventType",
    "LineageRelation",
    "MemoryKind",
    "MemoryStatus",
    "PolicyDecisionCode",
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
"""Optional upper and lower reference arms. Excluded from canonical comparisons."""


class MemoryKind(StrEnum):
    """Provenance class of a memory record."""

    SEED = "seed"
    """Supplied by the seed world before cycle 0. Identical across all arms."""

    GENERATED = "generated"
    """Writer output admitted at the end of the cycle that produced it."""

    SUMMARY = "summary"
    """Lossy compression of two or more source memories. Always carries lineage."""

    EXTERNAL = "external"
    """Information reintroduced by a recorded external event, never by recall.

    An evicted memory may not return on its own. When equivalent information
    re-enters an arm it enters as a new record of this kind, whose own provenance
    names the event that carried it.
    """


class MemoryStatus(StrEnum):
    """Whether a memory is still available to the writer, and if not, why."""

    ACTIVE = "active"
    """In the active set, counted against the budget, visible to the writer."""

    EVICTED = "evicted"
    """Removed by a policy decision. Nothing replaced it."""

    COMPRESSED = "compressed"
    """Folded into a summary. The summary's lineage names this record."""

    SUPERSEDED = "superseded"
    """Replaced by a later record that subsumes it, outside the budget mechanism."""


RETIRED_STATUSES: frozenset[MemoryStatus] = frozenset(
    {MemoryStatus.EVICTED, MemoryStatus.COMPRESSED, MemoryStatus.SUPERSEDED}
)
"""Statuses that mean a memory has left the active set. Complement of ACTIVE."""


class LineageRelation(StrEnum):
    """How a child memory relates to a parent it descends from."""

    COMPRESSED_INTO = "compressed_into"
    SUPERSEDED_BY = "superseded_by"


class CitationSource(StrEnum):
    """Which activity produced a citation.

    Only ``WRITER`` citations may change memory state. Interviews and evaluations
    are read-only probes: if answering an interview question refreshed a memory's
    recency, the act of measuring an arm would change what that arm goes on to
    remember, and the measurement would no longer be of the mechanism.
    """

    WRITER = "writer"
    INTERVIEW = "interview"
    EVALUATION = "evaluation"


class PolicyDecisionCode(StrEnum):
    """Why the policy engine reached the verdict it did for one arm-cycle.

    Every code here is produced by deterministic code, never by a model.
    """

    NO_ACTION_WITHIN_BUDGET = "no_action_within_budget"
    """The active set already fitted the budget. Nothing was retired."""

    EVICTED_OLDEST = "evicted_oldest"
    EVICTED_LEAST_RECENTLY_CITED = "evicted_least_recently_cited"
    EVICTED_LOWEST_RETENTION_DENSITY = "evicted_lowest_retention_density"
    HEAVY_HITTER_RESERVE_BROKEN = "heavy_hitter_reserve_broken"
    """The recency reserve had to be invaded to reach a legal budget."""

    EVICTED_OUTSIDE_WINDOW = "evicted_outside_window"
    EVICTED_RANDOM = "evicted_random"
    EVICTED_STATELESS = "evicted_stateless"
    RETAINED_ALL = "retained_all"
    """The full-memory reference arm retained everything, as it always must."""

    COMPRESSION_PLANNED = "compression_planned"
    """A summary is required before the arm can reach a legal budget."""

    COMPRESSION_COMMITTED = "compression_committed"
    """A supplied summary replaced its sources and the arm is now within budget."""

    SUMMARY_FALLBACK_FIFO = "summary_fallback_fifo"
    """No legal compression existed, so the configured FIFO fallback ran."""


class CycleStatus(StrEnum):
    """Lifecycle of a canonical cycle. Only ``COMMITTED`` cycles are immutable."""

    PENDING = "pending"
    RUNNING = "running"
    COMMITTED = "committed"
    FAILED = "failed"


class LedgerEventType(StrEnum):
    """The append-only facts a run records."""

    RUN_CREATED = "run_created"
    SEED_MEMORIES_INSTALLED = "seed_memories_installed"
    CYCLE_STARTED = "cycle_started"
    STIMULUS_DELIVERED = "stimulus_delivered"
    THOUGHT_GENERATED = "thought_generated"
    CITATIONS_VERIFIED = "citations_verified"
    POLICY_DECIDED = "policy_decided"
    SUMMARY_GENERATED = "summary_generated"
    MEMORY_RETIRED = "memory_retired"
    CYCLE_COMMITTED = "cycle_committed"
    METRIC_RECORDED = "metric_recorded"
    EXTERNAL_INFORMATION_REINTRODUCED = "external_information_reintroduced"
