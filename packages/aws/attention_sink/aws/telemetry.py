"""Structured CloudWatch logging, with a fixed field set and a redaction rule.

One JSON object per line, so a CloudWatch Logs Insights query can filter on
``arm_id`` or ``result_code`` without a regular expression that breaks the first time
a message is reworded.

The field list is closed on purpose. Every entry carries the same keys whether it
records a cycle, a model call, or a failure, because a dashboard built on fields that
appear only sometimes reports "no data" for an outage and for a quiet hour alike.

**Nothing here ever logs content.** Not a prompt, not a journal entry, not a memory,
not a stimulus, not an interview answer, and not an authorization header. A log line
is the least access-controlled artefact a deployment produces, and a system whose
whole subject is what an agent remembers must not leak the experiment through its own
telemetry. Identifiers, counts, durations, and result codes are what is left, and
they are enough to operate on.

That is enforced by :data:`FIELDS` and by nothing else. An allowlist is the only
mechanism here on purpose: a redaction pass that inspected values or field names
alongside it would be a second, weaker rule that could disagree with the first, and
the weaker one is the one somebody would come to rely on.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

__all__ = ["FIELDS", "StructuredLogger", "log_fields"]

FIELDS: tuple[str, ...] = (
    "service",
    "environment",
    "run_id",
    "cycle",
    "arm_id",
    "stage",
    "request_id",
    "model_id",
    "input_tokens",
    "output_tokens",
    "retry_count",
    "duration_ms",
    "result_code",
)
"""Every field a log line may carry, in the order they are written.

Closed rather than open: a caller that wants to record something else is asking to
record content, and the answer is a metric or a stored record, not a log line.
"""


def log_fields(**values: Any) -> dict[str, Any]:
    """Build one log record's fields, keeping only the ones a log line may carry.

    Anything outside :data:`FIELDS` is dropped rather than rejected, so a caller that
    reaches for a journal entry or a prompt gets a line without it instead of an
    exception in the middle of a cycle. Unset fields are omitted rather than written
    as null, because a CloudWatch Insights query filters on presence and a column of
    nulls is noise in every dashboard that reads it.
    """
    return {key: value for key, value in values.items() if key in FIELDS and value is not None}


@dataclass
class StructuredLogger:
    """A logger that emits one JSON object per line and nothing else.

    Holds the fields that are true for the whole invocation -- service, environment,
    run -- so a handler does not repeat them at every call site and cannot forget one
    on the line that matters.
    """

    service: str
    environment: str
    context: dict[str, Any] = field(default_factory=dict)
    stream: Any = None
    """Where lines are written. Defaults to stdout, which is what CloudWatch reads."""

    level: int = logging.INFO

    def bind(self, **values: Any) -> StructuredLogger:
        """A logger carrying these fields as well, for the rest of one scope."""
        return StructuredLogger(
            service=self.service,
            environment=self.environment,
            context={**self.context, **log_fields(**values)},
            stream=self.stream,
            level=self.level,
        )

    def _emit(self, level: str, event: str, values: Mapping[str, Any]) -> None:
        record = {
            "level": level,
            "event": event,
            "service": self.service,
            "environment": self.environment,
            **self.context,
            **values,
        }
        ordered = {key: record[key] for key in ("level", "event") if key in record}
        ordered.update({key: record[key] for key in FIELDS if key in record})
        line = json.dumps(ordered, sort_keys=False, separators=(",", ":"), ensure_ascii=False)
        print(line, file=self.stream if self.stream is not None else sys.stdout, flush=True)

    def info(self, event: str, **values: Any) -> None:
        """Record something that happened."""
        self._emit("INFO", event, log_fields(**values))

    def warning(self, event: str, **values: Any) -> None:
        """Record something that did not happen but was survivable."""
        self._emit("WARN", event, log_fields(**values))

    def error(self, event: str, **values: Any) -> None:
        """Record a failure, by code rather than by message.

        ``result_code`` rather than an exception string: a code can be alarmed on and
        a message cannot, and an exception's text is the most likely place for a
        fragment of visitor or model content to escape into a log.
        """
        self._emit("ERROR", event, log_fields(**values))
