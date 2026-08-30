"""What each arm lost, and whether losing it cost anything.

The Graveyard is derived from cycle snapshots rather than stored alongside them. A
snapshot already records what was retired, why, with what text, and what descended
from it; deriving the Graveyard means it can never disagree with the record it comes
from, and a bug here is a bug in a projection rather than a corruption of evidence.

The distinction the whole view exists for is between *evicted* and *compressed*. A
memory a summary still carries has not been forgotten -- the arm can still answer from
it -- and counting it as a loss would make the summarising arm look like it forgets
most and remembers most at the same time. ``genuinely_inaccessible`` is the flag every
downstream metric filters on.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.domain import ArmId, MemoryStatus
from attention_sink.pilot import ArmCycleSnapshot

__all__ = ["GraveyardEntry", "build_graveyard", "lineage_of"]


class GraveyardEntry(BaseModel):
    """One memory that left an arm's active set, and everything about the leaving."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    memory_id: str = Field(min_length=1)
    text: str = Field(min_length=1)
    memory_type: str = Field(min_length=1)
    birth_cycle: int = Field(ge=0)
    retirement_cycle: int = Field(ge=0)
    lifespan: int = Field(ge=0)
    status: MemoryStatus
    validated_citation_count: int = Field(ge=0)
    last_cited_cycle: int | None = None
    retirement_reason: str = Field(min_length=1)
    policy_version: str = Field(min_length=1)
    snapshot_evidence: str = Field(min_length=1)
    """The digest of the cycle snapshot that recorded this retirement."""

    summary_descendant_id: str | None = None
    """The summary that absorbed this memory, when one did."""

    genuinely_inaccessible: bool = True
    """Whether the arm has really lost this.

    False when a summary still carries it. Compression is not forgetting, and every
    metric that asks "did this arm lose something" filters on this flag."""

    nearest_future_echo_id: str | None = None
    """A later memory that resembles this one, when the echo analysis found one."""


def build_graveyard(
    run_id: str, snapshots: Sequence[ArmCycleSnapshot]
) -> tuple[GraveyardEntry, ...]:
    """Derive every Graveyard entry for one arm from its committed snapshots.

    Args:
        run_id: The run these snapshots belong to.
        snapshots: One arm's snapshots, in cycle order.

    Returns:
        One entry per retirement, in the order the retirements happened.
    """
    if not snapshots:
        return ()
    ordered = sorted(snapshots, key=lambda snapshot: snapshot.cycle)
    birth = _birth_cycles(ordered)
    citations = _citation_history(ordered)
    summary_parents = _summary_parents(ordered)

    entries: list[GraveyardEntry] = []
    for snapshot in ordered:
        for record in snapshot.retired_memories:
            compressed_into = summary_parents.get(record.memory_id)
            born = birth.get(record.memory_id, 0)
            cited = citations.get(record.memory_id, ())
            entries.append(
                GraveyardEntry(
                    run_id=run_id,
                    arm_id=snapshot.arm_id,
                    memory_id=record.memory_id,
                    text=record.text,
                    memory_type=_memory_type(record.memory_id, ordered),
                    birth_cycle=born,
                    retirement_cycle=snapshot.cycle,
                    lifespan=max(0, snapshot.cycle - born),
                    status=record.status,
                    validated_citation_count=len(cited),
                    last_cited_cycle=max(cited) if cited else None,
                    retirement_reason=record.reason.value,
                    policy_version=str(snapshot.policy_version),
                    snapshot_evidence=snapshot.snapshot_hash,
                    summary_descendant_id=compressed_into,
                    genuinely_inaccessible=compressed_into is None,
                )
            )
    return tuple(entries)


def _birth_cycles(snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, int]:
    """When each memory entered the active set.

    Seeds are born at cycle 0 and appear in the first snapshot's "before" set;
    everything else is born in the cycle that minted it.
    """
    born: dict[str, int] = {}
    first = snapshots[0]
    for memory_id in first.active_memory_ids_before:
        born.setdefault(memory_id, 0)
    for snapshot in snapshots:
        born.setdefault(snapshot.candidate_memory_id, snapshot.cycle)
        if snapshot.created_summary is not None:
            born.setdefault(snapshot.created_summary.memory_id, snapshot.cycle)
        for statistic in snapshot.memory_statistics_before_rebalance:
            born.setdefault(statistic.memory_id, statistic.birth_cycle)
    return born


def _citation_history(snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, tuple[int, ...]]:
    """Every cycle in which each memory was validly cited."""
    history: dict[str, list[int]] = {}
    for snapshot in snapshots:
        for citation in snapshot.validated_citations:
            history.setdefault(citation.memory_id, []).append(snapshot.cycle)
    return {memory_id: tuple(cycles) for memory_id, cycles in history.items()}


def _summary_parents(snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, str]:
    """Which summary absorbed each compressed memory."""
    absorbed: dict[str, str] = {}
    for snapshot in snapshots:
        summary = snapshot.created_summary
        if summary is None:
            continue
        for parent in summary.parent_memory_ids:
            absorbed[parent] = summary.memory_id
    return absorbed


def _memory_type(memory_id: str, snapshots: Sequence[ArmCycleSnapshot]) -> str:
    """The kind a memory was, read off the statistics that recorded it."""
    for snapshot in snapshots:
        for statistic in snapshot.memory_statistics_before_rebalance:
            if statistic.memory_id == memory_id:
                return statistic.memory_kind
    return "generated"


def lineage_of(memory_id: str, snapshots: Sequence[ArmCycleSnapshot]) -> dict[str, list[str]]:
    """The ancestry and descent of one memory, as far as the snapshots record it.

    Returns:
        A mapping with ``parents`` and ``children``. Both empty for a memory that
        was neither a summary nor absorbed into one.
    """
    parents: list[str] = []
    children: list[str] = []
    for snapshot in snapshots:
        summary = snapshot.created_summary
        if summary is None:
            continue
        if summary.memory_id == memory_id:
            parents.extend(summary.parent_memory_ids)
        if memory_id in summary.parent_memory_ids:
            children.append(summary.memory_id)
    return {"parents": parents, "children": children}
