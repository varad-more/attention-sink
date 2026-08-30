"""The run-cycle Lambda: one invocation, at most one cycle.

A thin adapter over :class:`~attention_sink.pilot.service.PilotService`. It decides
nothing about the experiment. What it does decide is whether this deployment is armed,
whether the run is in a state that may advance, and what a duplicate invocation means
-- and each of those is a question about operating a deployment rather than about
memory.

**One cycle per invocation, always.** Not a loop with a time budget: a loop that
stopped halfway would leave the run's next tick with a lock it did not take and a
prepared cycle it did not stage, and the reason each cycle is separately locked,
staged, and committed is so that no invocation ever has to reason about how far the
previous one got.

The handler returns a status rather than raising for every refusal that is not a
fault. A paused run, a complete run, a disabled deployment, and a lock held elsewhere
are all correct outcomes; raising would send them to the dead-letter queue and make an
operator investigate a deployment that is behaving exactly as configured.
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from attention_sink.aws.composition import Runtime, build_runtime
from attention_sink.aws.events import (
    CYCLE_COMPLETED_DETAIL_TYPE,
    CYCLE_COMPLETED_SOURCE,
    CycleCompleted,
)
from attention_sink.model_gateway import ConfigurationError
from attention_sink.pilot.budget import ModelCallBudgetExceeded
from attention_sink.pilot.repositories import LockNotHeld, PersistenceError
from attention_sink.pilot.service import CycleOutcome, RunNotFound, RunPaused, ServiceError

__all__ = ["SERVICE_NAME", "handler", "run_one_cycle"]

SERVICE_NAME = "run-cycle"


def handler(event: Any, context: Any = None) -> dict[str, Any]:
    """Advance the configured run by one cycle, or say why it did not.

    Args:
        event: An EventBridge Scheduler payload or a manual invocation. Recognised
            keys are ``run_id``, ``cycle``, and ``invocation_id``; all are optional.
        context: The Lambda context, read only for its request identifier.

    Returns:
        A JSON-serialisable summary. ``result_code`` is the field to alarm on.
    """
    runtime = build_runtime(SERVICE_NAME)
    request_id = getattr(context, "aws_request_id", None)
    payload = event if isinstance(event, dict) else {}
    return run_one_cycle(
        runtime,
        run_id=str(payload.get("run_id") or runtime.settings.run_id),
        cycle=_optional_cycle(payload.get("cycle")),
        invocation_id=str(payload.get("invocation_id") or request_id or uuid.uuid4().hex),
        request_id=request_id,
    )


def _optional_cycle(value: Any) -> int | None:
    """The cycle an invocation asked for, when it named one.

    A scheduler tick names none and means "whatever is next". A manual or retried
    invocation may name one, and naming one is what makes the request idempotent:
    a cycle already committed is reported rather than followed by another.
    """
    return None if value is None else int(value)


def run_one_cycle(
    runtime: Runtime,
    *,
    run_id: str,
    cycle: int | None = None,
    invocation_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """One guarded advance, with a structured record of what happened.

    Separated from :func:`handler` so a test, the operator command, and the Lambda
    all take the same path rather than three that agree today.
    """
    settings = runtime.settings
    log = runtime.logger.bind(run_id=run_id, request_id=request_id, stage="cycle")
    started = time.monotonic()

    try:
        settings.require_can_execute()
    except ConfigurationError as exc:
        log.warning("cycle.refused", result_code="execution_disabled")
        return _refused("execution_disabled", run_id, str(exc))

    service = runtime.service()
    try:
        run = service.get_run(run_id)
    except RunNotFound as exc:
        log.error("cycle.refused", result_code="run_not_found")
        return _refused("run_not_found", run_id, str(exc))

    if cycle is not None and cycle <= run.current_cycle:
        # A retried invocation naming a cycle the run has already committed. The
        # answer is the committed cycle, not a second one.
        log.info("cycle.duplicate", cycle=cycle, result_code="already_committed")
        return _committed(runtime, run_id=run_id, cycle=cycle, code="already_committed")

    ceiling = _ceiling(runtime)
    if run.current_cycle >= ceiling:
        log.info("cycle.complete", cycle=run.current_cycle, result_code="run_complete")
        return _refused(
            "run_complete",
            run_id,
            f"run {run_id} is at cycle {run.current_cycle} of a ceiling of {ceiling}",
            cycle=run.current_cycle,
        )

    try:
        outcome = service.run_next_cycle(run_id, invocation_id=invocation_id)
    except RunPaused as exc:
        log.info("cycle.paused", result_code="run_paused")
        return _refused("run_paused", run_id, str(exc))
    except LockNotHeld as exc:
        log.info("cycle.locked", result_code="lock_held_elsewhere")
        return _refused("lock_held_elsewhere", run_id, str(exc))
    except ModelCallBudgetExceeded as exc:
        # Raised before the call, so nothing was spent and no arm advanced. Its own
        # result code because it is the one failure an operator responds to by
        # changing the protocol rather than by looking for a bug, and because the
        # ModelCallLimitReached alarm is a metric filter on exactly this line.
        log.error("cycle.limit", result_code="model_call_limit")
        return _refused("model_call_limit", run_id, str(exc))
    except (ServiceError, PersistenceError) as exc:
        # A real fault. Logged by code, re-raised so the invocation fails, is retried,
        # and lands in the dead-letter queue if it keeps failing.
        log.error("cycle.failed", result_code="cycle_failed")
        raise RuntimeError(f"cycle of {run_id} failed: {exc}") from exc

    duration_ms = int((time.monotonic() - started) * 1000)
    _publish(runtime, outcome)
    # This cycle's own spend, not the run's running total. The alarm on this field is
    # called "abnormal token use", and a cumulative counter crosses any fixed
    # threshold eventually -- so logging the total made the alarm fire on cycle nine
    # of a healthy run and mean nothing thereafter. The run's totals are on the run.
    before, after = run.usage, outcome.run.usage
    log.info(
        "cycle.committed",
        cycle=outcome.cycle,
        duration_ms=duration_ms,
        input_tokens=max(after.input_tokens - before.input_tokens, 0),
        output_tokens=max(after.output_tokens - before.output_tokens, 0),
        retry_count=max(after.retries - before.retries, 0),
        result_code="committed",
    )
    return {
        "result_code": "committed",
        "run_id": run_id,
        "cycle": outcome.cycle,
        "committed_arms": [snapshot.arm_id.value for snapshot in outcome.snapshots],
        "reused_prepared_cycle": outcome.reused_prepared_cycle,
        "checkpoint_interviews": len(outcome.checkpoints),
        "run_status": outcome.run.status.value,
        "current_cycle": outcome.run.current_cycle,
        "duration_ms": duration_ms,
        "total_model_calls": after.total_calls,
    }


def _ceiling(runtime: Runtime) -> int:
    """The last cycle this deployment may advance to.

    The lower of the protocol's own maximum and any environment ceiling. Staging sets
    one deliberately short so that arming the scheduler by mistake costs a handful of
    cycles rather than a whole experiment's worth of model calls.
    """
    protocol_maximum = runtime.bundle.protocol.maximum_cycles
    configured = runtime.settings.maximum_cycles
    return protocol_maximum if configured is None else min(protocol_maximum, configured)


def _refused(code: str, run_id: str, reason: str, *, cycle: int | None = None) -> dict[str, Any]:
    """A correct non-advance, reported rather than raised."""
    body: dict[str, Any] = {"result_code": code, "run_id": run_id, "reason": reason}
    if cycle is not None:
        body["cycle"] = cycle
    return body


def _committed(runtime: Runtime, *, run_id: str, cycle: int, code: str) -> dict[str, Any]:
    """The cycle that is already there, described from the store."""
    snapshots = runtime.repository.list_cycle_snapshots(run_id, cycle=cycle)
    return {
        "result_code": code,
        "run_id": run_id,
        "cycle": cycle,
        "committed_arms": [snapshot.arm_id.value for snapshot in snapshots],
    }


def _publish(runtime: Runtime, outcome: CycleOutcome) -> None:
    """Announce the committed cycle so analysis can pick it up.

    Published after the commit, never before. An event for a cycle that then failed to
    commit would have analysis reading a cycle that does not exist, and the consumer
    re-checks the store precisely because a bus is a notification and not a record.
    """
    run = outcome.run
    event = CycleCompleted(
        run_id=run.run_id,
        cycle=outcome.cycle,
        run_kind=run.run_kind.value,
        committed_arms=tuple(snapshot.arm_id.value for snapshot in outcome.snapshots),
        snapshot_hashes={s.arm_id.value: s.snapshot_hash for s in outcome.snapshots},
        checkpoint=run.configuration.is_checkpoint(outcome.cycle),
        run_complete=run.is_complete,
        committed_at=run.updated_at.isoformat(),
    )
    entry: dict[str, Any] = {
        "Source": CYCLE_COMPLETED_SOURCE,
        "DetailType": CYCLE_COMPLETED_DETAIL_TYPE,
        "Detail": json.dumps(event.model_dump(mode="json")),
    }
    bus = runtime.settings.event_bus_name
    if bus:
        entry["EventBusName"] = bus
    runtime.events().put_events(Entries=[entry])
