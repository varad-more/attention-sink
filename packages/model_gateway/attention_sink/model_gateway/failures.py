"""Deciding what a provider failure was, and whether asking again could help.

Two questions, kept apart on purpose. *What went wrong* is a fact about the
exception and is recorded whatever we do next. *Whether to try again* is a policy,
and it is deliberately narrow: only failures that are transient, or that a second
request could plausibly repair, are retried. Retrying a validation error or an access
denial spends the budget to produce the same failure more slowly.

An exception this module does not recognise is re-raised untouched rather than
labelled with the nearest-looking code. A wrong error code in a run's record is worse
than an unfamiliar traceback, because it is the one a reader will believe.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from botocore.exceptions import ClientError, ReadTimeoutError
from botocore.exceptions import ConnectionError as BotoConnectionError
from pydantic import ValidationError
from strands.types.exceptions import (
    ContextWindowOverflowException,
    MaxTokensReachedException,
    ModelThrottledException,
    StructuredOutputException,
)

from attention_sink.model_gateway.observability import ModelErrorCode

__all__ = [
    "RETRYABLE_CODES",
    "ModelInvocationError",
    "Retrier",
    "RetriesExhausted",
    "RetryPolicy",
    "SchemaRepairNeeded",
    "classify",
    "is_retryable",
]

RETRYABLE_CODES: frozenset[ModelErrorCode] = frozenset(
    {
        ModelErrorCode.THROTTLING,
        ModelErrorCode.MODEL_TIMEOUT,
        ModelErrorCode.TRANSIENT_SERVER_ERROR,
        ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT,
    }
)
"""Codes worth another attempt on their own.

``TOKEN_LIMIT_EXCEEDED`` is absent, and that is the interesting case. When the
*provider* reports it, the request was too long and an identical retry would be too
long again. When *we* raise it, because a summary came back over the plan's ceiling,
the next attempt can be told to write less -- so it arrives as
:class:`SchemaRepairNeeded`, which is retryable by virtue of carrying a repair.
"""

_CLIENT_ERROR_CODES: Mapping[str, ModelErrorCode] = {
    "AccessDeniedException": ModelErrorCode.ACCESS_DENIED,
    "ExpiredTokenException": ModelErrorCode.ACCESS_DENIED,
    "UnrecognizedClientException": ModelErrorCode.ACCESS_DENIED,
    "InternalServerException": ModelErrorCode.TRANSIENT_SERVER_ERROR,
    "ModelErrorException": ModelErrorCode.TRANSIENT_SERVER_ERROR,
    "ModelNotReadyException": ModelErrorCode.TRANSIENT_SERVER_ERROR,
    "ModelStreamErrorException": ModelErrorCode.TRANSIENT_SERVER_ERROR,
    "ServiceUnavailableException": ModelErrorCode.TRANSIENT_SERVER_ERROR,
    "ModelTimeoutException": ModelErrorCode.MODEL_TIMEOUT,
    "ResourceNotFoundException": ModelErrorCode.UNSUPPORTED_MODEL,
    "ServiceQuotaExceededException": ModelErrorCode.THROTTLING,
    "ThrottlingException": ModelErrorCode.THROTTLING,
    "TooManyRequestsException": ModelErrorCode.THROTTLING,
    "ValidationException": ModelErrorCode.VALIDATION_ERROR,
}

_SERVER_ERROR_FLOOR = 500


class SchemaRepairNeeded(Exception):
    """A response validated but broke a rule the next attempt can be told about.

    Distinct from a schema violation the library caught: the response parsed, and
    what is wrong with it is something we can describe well enough for another
    attempt to avoid. The summarising role raises this when a summary comes back
    over the ceiling its plan set.
    """

    def __init__(self, hint: str, *, code: ModelErrorCode) -> None:
        """Record the repair instruction and the code this failure is filed under.

        Args:
            hint: What the next attempt must do differently. Appended to the data
                turn verbatim, so it must read as an instruction to the model.
            code: The code recorded if every attempt is exhausted.
        """
        super().__init__(hint)
        self.hint = hint
        self.code = code


class RetriesExhausted(Exception):
    """Every permitted attempt failed, or the first failure was not worth repeating."""

    def __init__(self, *, code: ModelErrorCode, attempts: int, cause: BaseException) -> None:
        """Record the terminal code and how many attempts were spent reaching it."""
        super().__init__(f"{code.value} after {attempts} attempt(s)")
        self.code = code
        self.attempts = attempts
        self.cause = cause


class ModelInvocationError(RuntimeError):
    """A model call failed, with the record of the attempt attached.

    The metadata is on the exception rather than returned separately because the
    call that failed is exactly the one whose cost, latency, and retry count a run
    still needs to account for.
    """

    def __init__(self, message: str, *, metadata: Any) -> None:
        """Bind a failure to the :class:`CallMetadata` describing the attempt."""
        super().__init__(message)
        self.metadata = metadata

    @property
    def code(self) -> ModelErrorCode | None:
        """The terminal error code, as recorded on the metadata."""
        code: ModelErrorCode | None = self.metadata.error_code
        return code


def classify(exc: BaseException) -> ModelErrorCode | None:
    """Return the code for ``exc``, or ``None`` if this module does not know it.

    ``None`` is a real answer. An unrecognised exception is re-raised as itself so
    that it is investigated, rather than filed under whichever code looked closest.
    """
    if isinstance(exc, SchemaRepairNeeded):
        return exc.code
    if isinstance(exc, ClientError):
        return _classify_client_error(exc)
    if isinstance(exc, ReadTimeoutError):
        return ModelErrorCode.MODEL_TIMEOUT
    if isinstance(exc, BotoConnectionError):
        return ModelErrorCode.TRANSIENT_SERVER_ERROR
    if isinstance(exc, ModelThrottledException):
        return ModelErrorCode.THROTTLING
    if isinstance(exc, ContextWindowOverflowException | MaxTokensReachedException):
        return ModelErrorCode.TOKEN_LIMIT_EXCEEDED
    if isinstance(exc, StructuredOutputException | ValidationError):
        return ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    return None


def _classify_client_error(exc: ClientError) -> ModelErrorCode:
    """Map a Bedrock error code, falling back to what the HTTP status implies."""
    response: Mapping[str, Any] = exc.response
    error = response.get("Error", {})
    code = error.get("Code", "") if isinstance(error, Mapping) else ""
    known = _CLIENT_ERROR_CODES.get(code)
    if known is not None:
        return known
    metadata = response.get("ResponseMetadata", {})
    status = metadata.get("HTTPStatusCode", 0) if isinstance(metadata, Mapping) else 0
    if isinstance(status, int) and status >= _SERVER_ERROR_FLOOR:
        return ModelErrorCode.TRANSIENT_SERVER_ERROR
    return ModelErrorCode.VALIDATION_ERROR


def is_retryable(exc: BaseException, code: ModelErrorCode) -> bool:
    """Whether another attempt could plausibly succeed where this one failed."""
    return isinstance(exc, SchemaRepairNeeded) or code in RETRYABLE_CODES


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Bounded exponential backoff with full jitter.

    Full jitter -- a uniform draw across the whole window rather than a fixed delay
    plus a wobble -- because six arms are invoked from one orchestration and would
    otherwise retry in lockstep, converting one throttle into a synchronised second
    one.
    """

    max_attempts: int = 4
    """Total attempts, first included. ``max_model_retries + 1``."""

    base_delay_seconds: float = 0.5
    max_delay_seconds: float = 20.0

    def delay_for(self, attempt: int, jitter: float) -> float:
        """Seconds to wait after the zero-based ``attempt`` that just failed.

        Args:
            attempt: Index of the attempt that failed, starting at zero.
            jitter: A draw in ``[0, 1)``. Supplied rather than drawn here, so a test
                can pin the delay without patching a random number generator.
        """
        window = min(self.max_delay_seconds, self.base_delay_seconds * (2.0**attempt))
        return window * jitter


@dataclass
class Retrier:
    """Runs one operation until it succeeds, is not worth repeating, or runs out.

    The clock and the generator are injected, and both default to the real thing. A
    test passes a no-op sleep, because a suite that waited out real backoff would be
    slow enough that nobody would run it; it also passes a seeded generator rather
    than patching the module-global one, which other code shares.
    """

    policy: RetryPolicy = field(default_factory=RetryPolicy)
    sleep: Callable[[float], None] = time.sleep
    rng: random.Random = field(default_factory=random.Random)

    def run[T](self, attempt: Callable[[int, str | None], T]) -> tuple[T, int]:
        """Call ``attempt`` until it succeeds, and report how many retries it took.

        Args:
            attempt: Called with the zero-based attempt index and the repair hint
                from the previous failure, if that failure carried one.

        Returns:
            The result, and the number of retries -- attempts after the first.

        Raises:
            RetriesExhausted: Every permitted attempt failed, or the first failure
                was one that retrying could not help.
            BaseException: Re-raised unchanged when :func:`classify` does not
                recognise it.
        """
        hint: str | None = None
        for index in range(self.policy.max_attempts):
            try:
                return attempt(index, hint), index
            except Exception as exc:
                code = classify(exc)
                if code is None:
                    raise
                last = self.policy.max_attempts - 1
                if index == last or not is_retryable(exc, code):
                    raise RetriesExhausted(code=code, attempts=index + 1, cause=exc) from exc
                hint = exc.hint if isinstance(exc, SchemaRepairNeeded) else None
                self.sleep(self.policy.delay_for(index, self.rng.random()))
        raise AssertionError("unreachable: the loop returns or raises on every iteration")
