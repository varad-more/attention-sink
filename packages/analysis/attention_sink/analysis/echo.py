"""Graveyard Echo: whether a new memory reaches back to something forgotten.

The measurement is a difference, not a similarity. A memory that resembles a
forgotten record might simply be about the same station, the same brother, the same
stopped clock -- so resemblance on its own is a property of the setting rather than a
property of the arm. What matters is whether the new text is closer to something the
arm *cannot* see than to anything it can:

    echo_delta = forgotten_similarity - active_similarity

A positive delta over the versioned threshold is the only thing worth a model's
opinion, and only then is one asked. Everything below it is classified without a call.

Compression is excluded from the forgotten set before any of this runs. A summarising
arm that "echoes" a memory its own summary still carries has not reconstructed
anything, and counting that would make the one arm designed to retain information look
like the one most haunted by losing it.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.analysis.graveyard import GraveyardEntry
from attention_sink.analysis.metrics import ECHO_THRESHOLD, METRIC_VERSION, EchoCategory
from attention_sink.domain import ArmId, Memory

__all__ = ["EchoMeasurement", "classify_echo", "measure_echo"]

Embedder = Callable[[str], Sequence[float]]
"""Text to vector. Deterministic in fixture mode, which is what makes this replayable."""


class EchoMeasurement(BaseModel):
    """One new memory measured against what its arm can and cannot still see."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    arm_id: ArmId
    cycle: int = Field(ge=1)
    memory_id: str = Field(min_length=1)
    forgotten_similarity: float
    active_similarity: float
    echo_delta: float
    nearest_forgotten_memory_id: str | None = None
    nearest_active_memory_id: str | None = None
    category: EchoCategory
    threshold: float = ECHO_THRESHOLD
    metric_version: str = METRIC_VERSION
    evaluator_version: str | None = None
    evidence_excerpt: str = ""
    """The forgotten text the resemblance is to, so a reader can judge it themselves."""


def _similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, the complement of the distance used elsewhere."""
    from attention_sink.analysis.metrics import cosine_distance

    return 1.0 - cosine_distance(left, right)


def _nearest(
    vector: Sequence[float], candidates: Sequence[tuple[str, str, Sequence[float]]]
) -> tuple[float, str | None, str]:
    """The closest candidate to ``vector``: its similarity, identifier, and text."""
    best: tuple[float, str | None, str] = (-1.0, None, "")
    for memory_id, text, candidate in candidates:
        score = _similarity(vector, candidate)
        if score > best[0]:
            best = (score, memory_id, text)
    return best if best[1] is not None else (0.0, None, "")


def measure_echo(
    *,
    run_id: str,
    arm_id: ArmId,
    cycle: int,
    memory_id: str,
    text: str,
    graveyard: Sequence[GraveyardEntry],
    active: Sequence[Memory],
    embed: Embedder,
    evaluate: Callable[[str, Sequence[str]], tuple[str, float, str]] | None = None,
    threshold: float = ECHO_THRESHOLD,
) -> EchoMeasurement:
    """Measure one new memory against the forgotten and the still-available.

    Only genuinely inaccessible entries count as forgotten. A compressed memory whose
    summary the arm still holds is deliberately excluded, and a resemblance to one is
    reported as ``COMPRESSED_ECHO`` rather than as a reconstruction.

    Args:
        run_id: The run this measurement belongs to.
        arm_id: The arm whose memory is being measured.
        cycle: The cycle the memory was written in.
        memory_id: The new memory's identifier.
        text: The new memory's text.
        graveyard: This arm's Graveyard entries up to this cycle.
        active: What the arm can still see.
        embed: The embedding provider, deterministic in fixture mode.
        evaluate: Asked only when the delta crosses ``threshold``.
        threshold: The versioned delta above which a model is worth consulting.
    """
    vector = embed(text)
    inaccessible = [
        (e.memory_id, e.text, embed(e.text)) for e in graveyard if e.genuinely_inaccessible
    ]
    retained = [
        (e.memory_id, e.text, embed(e.text)) for e in graveyard if not e.genuinely_inaccessible
    ]
    available = [(m.memory_id, m.text, embed(m.text)) for m in active if m.memory_id != memory_id]

    forgotten_score, forgotten_id, forgotten_text = _nearest(vector, inaccessible)
    active_score, active_id, _ = _nearest(vector, available)
    delta = forgotten_score - active_score

    compressed_score, compressed_id, compressed_text = _nearest(vector, retained)
    if compressed_id is not None and compressed_score > forgotten_score:
        return EchoMeasurement(
            run_id=run_id,
            arm_id=arm_id,
            cycle=cycle,
            memory_id=memory_id,
            forgotten_similarity=forgotten_score,
            active_similarity=active_score,
            echo_delta=delta,
            nearest_forgotten_memory_id=forgotten_id,
            nearest_active_memory_id=active_id,
            category=EchoCategory.COMPRESSED_ECHO,
            threshold=threshold,
            evidence_excerpt=compressed_text[:280],
        )

    category, evaluator_version = classify_echo(
        delta=delta,
        threshold=threshold,
        passage=text,
        reference=forgotten_text,
        evaluate=evaluate,
    )
    return EchoMeasurement(
        run_id=run_id,
        arm_id=arm_id,
        cycle=cycle,
        memory_id=memory_id,
        forgotten_similarity=forgotten_score,
        active_similarity=active_score,
        echo_delta=delta,
        nearest_forgotten_memory_id=forgotten_id,
        nearest_active_memory_id=active_id,
        category=category,
        threshold=threshold,
        evaluator_version=evaluator_version,
        evidence_excerpt=forgotten_text[:280],
    )


def classify_echo(
    *,
    delta: float,
    threshold: float,
    passage: str,
    reference: str,
    evaluate: Callable[[str, Sequence[str]], tuple[str, float, str]] | None = None,
) -> tuple[EchoCategory, str | None]:
    """Decide what a delta means, asking a model only when the delta earns it.

    Below the threshold the answer is deterministic and no call is made: either the
    new memory is closer to something the arm can still see (``UNRELATED``), or it is
    similar to both (``SHARED_MOTIF_ONLY``), which is what a shared setting looks
    like. Above the threshold the resemblance is specific enough that only reading
    the two texts can tell reconstruction from coincidence.

    Returns:
        The category, and the evaluator version when one was consulted.
    """
    if delta < threshold or not reference:
        return (EchoCategory.UNRELATED if delta <= 0.0 else EchoCategory.SHARED_MOTIF_ONLY), None
    if evaluate is None:
        return EchoCategory.PARTIAL_RECONSTRUCTION, None
    label, _score, version = evaluate(passage, [reference])
    mapped = {
        "supported": EchoCategory.GENUINE_RECONSTRUCTION,
        "partially_supported": EchoCategory.PARTIAL_RECONSTRUCTION,
        "contradicted": EchoCategory.CONTRADICTORY_RECONSTRUCTION,
        "unsupported": EchoCategory.SHARED_MOTIF_ONLY,
    }
    return mapped.get(label, EchoCategory.PARTIAL_RECONSTRUCTION), version
