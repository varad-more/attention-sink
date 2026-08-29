"""What every model call reports about itself, whether it succeeded or not.

A generation with no record of which model produced it, in which Region, at what
cost, after how many retries, is not evidence. This module holds the vocabulary and
the record; :mod:`attention_sink.model_gateway.failures` holds the rules that decide
which code a given exception earns.

Nothing here holds a credential, a header, or a body. :func:`request_id_of` is the
single place a provider response is read for metadata, and it takes one field.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "CallMetadata",
    "CallOutcome",
    "ModelErrorCode",
    "ModelRole",
    "request_id_of",
]


class ModelRole(StrEnum):
    """Which part of the experiment a call belongs to."""

    WRITER = "writer"
    AUDITOR = "auditor"
    SUMMARIZER = "summarizer"
    INTERVIEWER = "interviewer"
    EVALUATOR = "evaluator"
    EMBEDDING = "embedding"
    TOKEN_COUNTER = "token_counter"  # noqa: S105 - a counting role, not a secret


class ModelErrorCode(StrEnum):
    """Why a call failed, in terms an operator can act on."""

    VALIDATION_ERROR = "validation_error"
    """The request was malformed. Retrying an identical request cannot help."""

    ACCESS_DENIED = "access_denied"
    """Credentials or policy forbid this call. A deployment problem, not a run one."""

    THROTTLING = "throttling"
    MODEL_TIMEOUT = "model_timeout"
    TRANSIENT_SERVER_ERROR = "transient_server_error"

    UNSUPPORTED_MODEL = "unsupported_model"
    """The configured model does not exist here. Configuration, not capacity."""

    MALFORMED_STRUCTURED_OUTPUT = "malformed_structured_output"
    """The response did not fit its schema. Repairable by asking again."""

    TOKEN_LIMIT_EXCEEDED = "token_limit_exceeded"  # noqa: S105 - a budget, not a secret
    """Something did not fit a token limit: the request, or a summary that came back
    longer than the plan allowed."""


class CallOutcome(StrEnum):
    """Whether the call produced a usable, schema-valid result."""

    SUCCESS = "success"
    FAILURE = "failure"


class CallMetadata(BaseModel):
    """The complete record of one model call.

    Recorded for failures as well as successes, and carried on the exception when a
    call fails, because a run that spent an hour being throttled should look
    different in the record from one that was never attempted.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    role: ModelRole
    model_id: str = Field(min_length=1, max_length=256)
    region: str = Field(min_length=1, max_length=64)
    outcome: CallOutcome
    latency_ms: int = Field(ge=0)
    retry_count: int = Field(ge=0)
    """Attempts *after* the first. Zero means it worked, or failed, first time."""

    simulated: bool
    """True when a fixture produced this. Travels with the record, never inferred."""

    request_id: str | None = None
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    prompt_version: str | None = None
    prompt_hash: str | None = None
    stop_reason: str | None = None
    error_code: ModelErrorCode | None = None

    @model_validator(mode="after")
    def _require_consistent_outcome(self) -> Self:
        failed = self.outcome is CallOutcome.FAILURE
        if failed and self.error_code is None:
            msg = f"{self.role.value} call failed but names no error code"
            raise ValueError(msg)
        if not failed and self.error_code is not None:
            msg = f"{self.role.value} call succeeded but names error {self.error_code.value}"
            raise ValueError(msg)
        return self


def request_id_of(response: Mapping[str, Any]) -> str | None:
    """Extract the provider's request identifier, and nothing else.

    The single point at which a provider response is read for observability. Only
    ``ResponseMetadata.RequestId`` is taken: the rest of that block carries HTTP
    headers, and headers carry authorization material that must never reach a log.
    """
    metadata = response.get("ResponseMetadata")
    if not isinstance(metadata, Mapping):
        return None
    request_id = metadata.get("RequestId")
    return request_id if isinstance(request_id, str) and request_id else None
