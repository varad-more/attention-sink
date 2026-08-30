"""The single-table key layout, in one place so nothing has to guess at it.

Every logical record the pilot stores gets a partition key and a sort key here, and
nowhere else. A key format spelled out at the call site would drift the first time
somebody added a record type, and a drifted sort key is not a bug that shows up as an
error -- it shows up as a query that quietly returns nothing.

Two rules shape the layout.

**Everything about one run shares one partition.** ``RUN#{run_id}`` holds the run's
head, six arm states, every prepared cycle, every snapshot, every interview, every
metric, the analysis markers, and the export manifests. That is what lets a reader
fetch a cycle, a checkpoint, or a whole run's metrics with one ``Query`` and no
``Scan``.

**Cycle numbers are zero-padded.** DynamoDB sorts sort keys as bytes, so ``CYCLE#9``
sorts after ``CYCLE#10``. Six digits is four more than the pilot's twenty-four cycles
needs and costs five bytes, which is the right trade when the alternative is a range
query that silently returns the wrong window.

The caches are deliberately *not* run-scoped. An embedding and a token count are
functions of a model and a piece of text; keying them by run would pay for the same
vector once per run and make two runs of one protocol cost twice as much for numbers
that are identical by construction.
"""

from __future__ import annotations

from attention_sink.domain import content_hash

__all__ = [
    "CROSS_ARM",
    "CYCLE_DIGITS",
    "META_SK",
    "RUNS_PARTITION",
    "analysis_sk",
    "analysis_status_sk",
    "arm_snapshot_index_pk",
    "arm_state_sk",
    "artifact_sk",
    "cache_sk",
    "cycle_of_snapshot_sk",
    "embedding_pk",
    "export_sk",
    "interview_prefix",
    "interview_sk",
    "metric_prefix",
    "metric_sk",
    "model_hash",
    "prepared_sk",
    "run_pk",
    "run_sort_key",
    "snapshot_prefix",
    "snapshot_sk",
    "token_pk",
]

CYCLE_DIGITS = 6
"""Zero-padding width for every cycle number that appears in a sort key."""

META_SK = "META"
"""The sort key of a run's mutable head. Also carries the cycle lock."""

RUNS_PARTITION = "RUNS"
"""The index partition every run's head is listed under, so ``list_runs`` is a
``Query`` on one partition rather than a ``Scan`` of the table."""

CROSS_ARM = "ALL"
"""The arm segment of a metric that belongs to the run rather than to one arm."""


def _padded(cycle: int) -> str:
    return f"{cycle:0{CYCLE_DIGITS}d}"


# --------------------------------------------------------------------- runs


def run_pk(run_id: str) -> str:
    """The partition every record about one run lives in."""
    return f"RUN#{run_id}"


def run_sort_key(created_at: str, run_id: str) -> str:
    """A run's position in the newest-first listing.

    Creation time first so the index sorts chronologically, the identifier second so
    two runs created in the same microsecond still have a total order.
    """
    return f"{created_at}#{run_id}"


# ---------------------------------------------------------------- arm state


def arm_state_sk(arm_id: str) -> str:
    """One arm's current memory, as of the last committed cycle."""
    return f"ARM#{arm_id}#STATE"


ARM_STATE_PREFIX = "ARM#"
"""Prefix that selects every arm's current state in one query."""


# ------------------------------------------------------------------ cycles


def prepared_sk(cycle: int) -> str:
    """The staged, not-yet-committed result of one cycle."""
    return f"CYCLE#{_padded(cycle)}#PREPARED"


def snapshot_sk(cycle: int, arm_id: str) -> str:
    """One arm's immutable record of one committed cycle."""
    return f"CYCLE#{_padded(cycle)}#ARM#{arm_id}#SNAPSHOT"


def snapshot_prefix(cycle: int | None = None) -> str:
    """Every snapshot in a run, or every snapshot of one cycle."""
    return "CYCLE#" if cycle is None else f"CYCLE#{_padded(cycle)}#ARM#"


def cycle_of_snapshot_sk(sort_key: str) -> int | None:
    """The cycle a sort key belongs to, or None when it is not a snapshot.

    Used by ``list_completed_cycles``, which projects sort keys rather than whole
    snapshots: reading twenty kilobytes per arm to learn six integers would make the
    cheapest question in the API the most expensive one.
    """
    if not sort_key.startswith("CYCLE#") or not sort_key.endswith("#SNAPSHOT"):
        return None
    return int(sort_key.split("#")[1])


def arm_snapshot_index_pk(run_id: str, arm_id: str) -> str:
    """The index partition holding one arm's snapshots, in cycle order.

    The table's own sort key is cycle-major, so one arm's history is scattered across
    every cycle prefix. The Graveyard reads exactly that history for every arm on
    every request, which is the one access pattern the main key cannot serve.
    """
    return f"RUN#{run_id}#ARM#{arm_id}"


# -------------------------------------------------------------- interviews


def interview_sk(cycle: int, arm_id: str) -> str:
    """One arm's checkpoint interview."""
    return f"INTERVIEW#{_padded(cycle)}#ARM#{arm_id}"


def interview_prefix(cycle: int | None = None) -> str:
    """Every interview in a run, or every interview at one checkpoint."""
    return "INTERVIEW#" if cycle is None else f"INTERVIEW#{_padded(cycle)}#ARM#"


# ----------------------------------------------------------------- metrics


def metric_sk(metric_name: str, cycle: int, arm_id: str | None) -> str:
    """One scored metric. ``arm_id`` of None is a metric about the whole run."""
    return f"METRIC#{metric_name}#CYCLE#{_padded(cycle)}#ARM#{arm_id or CROSS_ARM}"


def metric_prefix(metric_name: str | None = None, cycle: int | None = None) -> str:
    """The narrowest prefix that covers a metric query.

    Name then cycle, because that is the order the sort key puts them in: narrowing
    by cycle without a name would need a filter over every metric in the run.
    """
    if metric_name is None:
        return "METRIC#"
    if cycle is None:
        return f"METRIC#{metric_name}#"
    return f"METRIC#{metric_name}#CYCLE#{_padded(cycle)}#"


# ---------------------------------------------------------------- analysis


def analysis_sk(cycle: int) -> str:
    """The marker saying analysis has finished for one committed cycle.

    Written by the analysis Lambda and checked by it before it starts. This is what
    makes a redelivered ``cycle-completed`` event cost one ``GetItem`` instead of a
    second full pass over the run.
    """
    return f"ANALYSIS#{_padded(cycle)}"


def analysis_status_sk(analysis_name: str) -> str:
    """How far one named analysis has got across the whole run.

    Sorts after every per-cycle marker, because ``N`` is above ``0`` in ASCII. That
    is deliberate: a prefix query for the markers must not pick up the summary.
    """
    return f"ANALYSIS#NAME#{analysis_name}"


def artifact_sk(name: str) -> str:
    """One derived analysis document, stored under a stable name."""
    return f"ARTIFACT#{name}"


# ------------------------------------------------------------------ export


def export_sk(export_id: str) -> str:
    """One completed export's manifest."""
    return f"EXPORT#{export_id}"


EXPORT_PREFIX = "EXPORT#"
"""Prefix that selects every export manifest recorded for a run."""


# ------------------------------------------------------------------ caches


def model_hash(model_identity: str) -> str:
    """A short, stable partition segment for one model or counter version.

    Hashed rather than used verbatim because a Bedrock model identifier carries
    colons and slashes, and a key that reads like a path invites somebody to parse
    it back out. Nothing needs to: the identity is stored in the item as well.
    """
    return content_hash(model_identity).removeprefix("sha256:")[:32]


def embedding_pk(model_identity: str) -> str:
    """The partition holding every vector one embedding model has produced."""
    return f"EMBEDDING#{model_hash(model_identity)}"


def token_pk(counter_version: str) -> str:
    """The partition holding every count one token counter has produced."""
    return f"TOKEN#{model_hash(counter_version)}"


def cache_sk(content: str) -> str:
    """The cached entry for one piece of content, within a model's partition."""
    return f"CONTENT#{content}"
