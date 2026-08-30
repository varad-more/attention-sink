"""What a cycle is allowed to spend on models, checked before it spends it.

A pilot cycle has a known shape: one writer call per arm, at most a couple of
Dreamer summaries for the one arm that compresses, and nothing else. Stating that as
a ceiling and enforcing it *before* each call turns a runaway loop into a stopped run
instead of a bill, and makes "this cycle made an evaluator call" a test failure rather
than something noticed later in a log.

The counters are guarded by a lock because arms are generated concurrently. Bounded
concurrency without a lock here would let two arms pass the same last remaining slot.
"""

from __future__ import annotations

import threading
from collections import Counter
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.model_gateway import CallMetadata, ModelRole
from attention_sink.pilot.protocol import ModelCallLimits

__all__ = ["CallLedgerEntry", "ModelCallBudget", "ModelCallBudgetExceeded", "ModelUsage"]


class ModelCallBudgetExceeded(RuntimeError):
    """A call was requested that this cycle or this run is not allowed to make.

    Raised *before* the call. Nothing has been spent when this surfaces, which is
    what makes it safe for the engine to treat it as an ordinary arm failure and
    leave every arm's state untouched.
    """


class CallLedgerEntry(BaseModel):
    """One model call, attributed to the cycle, arm, and operation that made it.

    A cumulative total answers "did this run overspend". It cannot answer "which arm
    spent the Dreamer calls on cycle 14", which is the question an unexpected bill or
    an unexpectedly divergent arm actually raises.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    run_id: str = Field(min_length=1)
    cycle: int = Field(ge=0)
    arm_id: str | None = None
    """The arm this call was made for, or None for a call the run made as a whole."""

    operation: str = Field(min_length=1)
    checkpoint: bool = False


class ModelUsage(BaseModel):
    """What a run has actually spent, cumulatively and per call."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    calls_by_role: dict[str, int] = Field(default_factory=dict)
    ledger: tuple[CallLedgerEntry, ...] = ()
    """Every call in the order it was claimed. See :class:`CallLedgerEntry`."""

    total_calls: int = Field(default=0, ge=0)
    failed_calls: int = Field(default=0, ge=0)
    simulated_calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    retries: int = Field(default=0, ge=0)


@dataclass
class ModelCallBudget:
    """The per-cycle and per-run ceilings, and the tally of what was spent.

    One instance per run. :meth:`open_cycle` resets the per-cycle counters and is the
    only way to move to a new cycle, so a cycle cannot silently inherit the previous
    one's remaining allowance.
    """

    limits: ModelCallLimits
    run_id: str = "pilot_local"
    cycle: int = 0
    checkpoint: bool = False
    _ledger: list[CallLedgerEntry] = field(default_factory=list, repr=False)
    _cycle_calls: Counter[ModelRole] = field(default_factory=Counter, repr=False)
    _run_calls: Counter[ModelRole] = field(default_factory=Counter, repr=False)
    _failed: int = field(default=0, repr=False)
    _simulated: int = field(default=0, repr=False)
    _input_tokens: int = field(default=0, repr=False)
    _output_tokens: int = field(default=0, repr=False)
    _retries: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def open_cycle(self, cycle: int, *, checkpoint: bool = False) -> None:
        """Begin ``cycle`` with a fresh per-cycle allowance."""
        with self._lock:
            self.cycle = cycle
            self.checkpoint = checkpoint
            self._cycle_calls = Counter()

    def allowance(self, role: ModelRole) -> int:
        """How many calls of ``role`` this cycle may make in total.

        The interviewer is the one role whose allowance depends on the cycle: a
        normal cycle may make none, and a checkpoint may interview every arm. Every
        other role has one number for the whole run.
        """
        limits = self.limits
        if role is ModelRole.INTERVIEWER:
            return (
                limits.interview_calls_per_checkpoint
                if self.checkpoint
                else limits.interview_calls_per_cycle
            )
        return {
            ModelRole.WRITER: limits.writer_calls_per_cycle,
            ModelRole.SUMMARIZER: limits.summary_calls_per_cycle,
            ModelRole.EVALUATOR: limits.evaluator_calls_per_cycle,
            ModelRole.TOKEN_COUNTER: limits.token_count_calls_per_cycle,
        }.get(role, 0)

    def remaining(self, role: ModelRole) -> int:
        """Calls of ``role`` still available in this cycle."""
        with self._lock:
            return max(self.allowance(role) - self._cycle_calls[role], 0)

    def spend(self, role: ModelRole, *, arm_id: str | None = None) -> None:
        """Claim one call of ``role``, or refuse before anything is invoked.

        Args:
            role: The operation the call performs.
            arm_id: The arm the call is made for, when it is made for one.

        Raises:
            ModelCallBudgetExceeded: This cycle has no allowance left for ``role``,
                or the run has reached its total ceiling.
        """
        with self._lock:
            allowed = self.allowance(role)
            spent = self._cycle_calls[role]
            if spent >= allowed:
                msg = (
                    f"cycle {self.cycle} may make {allowed} {role.value} call(s) and has "
                    f"already made {spent}; refusing to call the model"
                )
                raise ModelCallBudgetExceeded(msg)
            total = sum(self._run_calls.values())
            if total >= self.limits.max_model_calls_per_run:
                msg = (
                    f"this run is limited to {self.limits.max_model_calls_per_run} model "
                    f"calls and has made {total}; refusing to call the model"
                )
                raise ModelCallBudgetExceeded(msg)
            self._cycle_calls[role] += 1
            self._run_calls[role] += 1
            self._ledger.append(
                CallLedgerEntry(
                    run_id=self.run_id,
                    cycle=self.cycle,
                    arm_id=arm_id,
                    operation=role.value,
                    checkpoint=self.checkpoint,
                )
            )

    def record(self, metadata: CallMetadata) -> None:
        """Fold one completed call's metadata into the run tally.

        Separate from :meth:`spend` because a call that failed still cost time,
        tokens, and an allowance slot. Both halves are recorded, so a run that spent
        its budget on retries looks different from one that spent it on results.
        """
        with self._lock:
            self._simulated += 1 if metadata.simulated else 0
            self._failed += 1 if metadata.error_code is not None else 0
            self._input_tokens += metadata.input_tokens or 0
            self._output_tokens += metadata.output_tokens or 0
            self._retries += metadata.retry_count

    @property
    def usage(self) -> ModelUsage:
        """The cumulative tally, as the immutable record a snapshot carries."""
        with self._lock:
            return ModelUsage(
                calls_by_role={
                    role.value: count for role, count in sorted(self._run_calls.items())
                },
                ledger=tuple(self._ledger),
                total_calls=sum(self._run_calls.values()),
                failed_calls=self._failed,
                simulated_calls=self._simulated,
                input_tokens=self._input_tokens,
                output_tokens=self._output_tokens,
                retries=self._retries,
            )
