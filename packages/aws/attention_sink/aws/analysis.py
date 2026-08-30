"""The analysis Lambda: what a committed cycle means, computed after the fact.

Triggered by ``CycleCompleted`` on the bus, and the first thing it does is not trust
it. An event is a notification; the store is the record. The snapshots for the named
cycle are read back and their digests compared against the ones the event carried,
because analysing a cycle that did not commit -- or that committed differently -- would
produce metrics nothing else in the system agrees with.

**It never writes a snapshot and never revises one.** It computes, it interviews at a
checkpoint if the cycle Lambda did not get that far, and it stores metrics, derived
artefacts, and a marker saying the cycle has been analysed. A committed record is
finished, and analysis is a reader of it.

**Redelivery is normal.** EventBridge delivers at least once, so the same cycle
arrives twice more often than not. The marker is claimed with a conditional write
before the work starts, and released again if the work fails, so a duplicate costs one
``PutItem`` and a crash does not make a cycle permanently unanalysed.
"""

from __future__ import annotations

import time
from typing import Any

from pydantic import ValidationError

from attention_sink.aws.composition import Runtime, build_runtime
from attention_sink.aws.events import CycleCompleted

__all__ = ["SERVICE_NAME", "analyse_cycle", "handler"]

SERVICE_NAME = "analysis"


def handler(event: Any, context: Any = None) -> dict[str, Any]:
    """Analyse the cycle an EventBridge event says has committed.

    Args:
        event: An EventBridge event whose ``detail`` is a
            :class:`~attention_sink.aws.events.CycleCompleted`.
        context: The Lambda context, read only for its request identifier.

    Returns:
        A JSON-serialisable summary. ``result_code`` is the field to alarm on.

    Raises:
        ValueError: The event is not a cycle-completed event. Permanent: a
            redelivery of a malformed event is malformed too, so it belongs in the
            dead-letter queue rather than in a retry loop.
    """
    runtime = build_runtime(SERVICE_NAME)
    detail = event.get("detail") if isinstance(event, dict) else None
    try:
        completed = CycleCompleted.from_detail(detail)
    except ValidationError as exc:
        runtime.logger.error("analysis.malformed_event", result_code="malformed_event")
        msg = "the event delivered to the analysis handler is not a cycle-completed event"
        raise ValueError(msg) from exc
    return analyse_cycle(runtime, completed, request_id=getattr(context, "aws_request_id", None))


def analyse_cycle(
    runtime: Runtime, completed: CycleCompleted, *, request_id: str | None = None
) -> dict[str, Any]:
    """Verify, claim, analyse, and record one committed cycle."""
    log = runtime.logger.bind(
        run_id=completed.run_id, cycle=completed.cycle, request_id=request_id, stage="analysis"
    )
    started = time.monotonic()
    repository = runtime.repository

    mismatch = _verify(runtime, completed)
    if mismatch is not None:
        log.error("analysis.uncommitted", result_code="cycle_not_committed")
        return {
            "result_code": "cycle_not_committed",
            "run_id": completed.run_id,
            "cycle": completed.cycle,
            "reason": mismatch,
        }

    if not repository.mark_cycle_analysed(
        completed.run_id,
        cycle=completed.cycle,
        detail={"claimed_at": runtime.repository.clock().isoformat(), "request_id": request_id},
    ):
        log.info("analysis.duplicate", result_code="already_analysed")
        return {
            "result_code": "already_analysed",
            "run_id": completed.run_id,
            "cycle": completed.cycle,
        }

    try:
        interviews = _checkpoint(runtime, completed)
        result = runtime.analysis().analyse_run(completed.run_id)
        for name, payload in _artifact_payloads(result).items():
            repository.store_analysis_artifact(completed.run_id, name=name, payload=payload)
    except Exception:
        # Release the claim so a retry can do the work. Without this a transient
        # failure would leave the cycle marked analysed and permanently unanalysed.
        repository.release_cycle_analysis(completed.run_id, cycle=completed.cycle)
        log.error("analysis.failed", result_code="analysis_failed")
        raise

    duration_ms = int((time.monotonic() - started) * 1000)
    repository.mark_cycle_analysed(
        completed.run_id,
        cycle=completed.cycle,
        detail={
            "metrics": len(result.metrics),
            "graveyard": len(result.graveyard),
            "echoes": len(result.echoes),
            "contradictions": len(result.contradictions),
            "checkpoint_interviews": interviews,
        },
    )
    log.info("analysis.completed", duration_ms=duration_ms, result_code="analysed")
    return {
        "result_code": "analysed",
        "run_id": completed.run_id,
        "cycle": completed.cycle,
        "metrics": len(result.metrics),
        "graveyard": len(result.graveyard),
        "echoes": len(result.echoes),
        "contradictions": len(result.contradictions),
        "checkpoint_interviews": interviews,
        "duration_ms": duration_ms,
    }


def _verify(runtime: Runtime, completed: CycleCompleted) -> str | None:
    """Why the store disagrees with the event, or None when it agrees.

    Both halves matter. A cycle missing an arm is a cycle that did not commit, which
    the transaction should make impossible and which is therefore worth saying out
    loud if it ever happens. A digest that differs means the event describes a
    different generation of the same cycle number, and analysing it would attribute
    one run's numbers to another's records.
    """
    snapshots = runtime.repository.list_cycle_snapshots(completed.run_id, cycle=completed.cycle)
    stored = {snapshot.arm_id.value: snapshot.snapshot_hash for snapshot in snapshots}
    missing = sorted(set(completed.committed_arms) - set(stored))
    if missing:
        return f"cycle {completed.cycle} has no stored snapshot for {', '.join(missing)}"
    differing = sorted(
        arm for arm, digest in completed.snapshot_hashes.items() if stored.get(arm) != digest
    )
    if differing:
        return (
            f"cycle {completed.cycle} is stored with different content for "
            f"{', '.join(differing)} than the event announced"
        )
    return None


def _checkpoint(runtime: Runtime, completed: CycleCompleted) -> int:
    """Interview every arm this checkpoint has not yet been asked.

    Idempotent, and usually a no-op: the cycle handler interviews inline immediately
    after committing. It is here for the case that handler timed out in between, so a
    checkpoint is not lost because the process that committed it ran out of time.
    """
    if not completed.checkpoint:
        return 0
    return len(runtime.service().run_checkpoint(completed.run_id, cycle=completed.cycle))


def _artifact_payloads(result: Any) -> dict[str, dict[str, Any]]:
    """The four derived documents, in the shape the read API expects them."""
    return {
        "divergence": {"matrices": result.divergence},
        "echoes": {"items": [echo.model_dump(mode="json") for echo in result.echoes]},
        "contradictions": {
            "items": [finding.model_dump(mode="json") for finding in result.contradictions]
        },
        "question_scores": {
            "items": [score.model_dump(mode="json") for score in result.question_scores]
        },
    }
