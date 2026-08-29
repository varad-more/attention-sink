"""Provider failures are classified once, and retried only where retrying helps."""

from __future__ import annotations

import random
from typing import Any

import pytest
from botocore.exceptions import ClientError, ReadTimeoutError
from pydantic import BaseModel, ValidationError
from strands.types.exceptions import (
    ContextWindowOverflowException,
    MaxTokensReachedException,
    ModelThrottledException,
    StructuredOutputException,
)

from attention_sink.model_gateway import (
    ModelErrorCode,
    Retrier,
    RetriesExhausted,
    RetryPolicy,
    SchemaRepairNeeded,
    classify,
    is_retryable,
)


def client_error(code: str, *, status: int = 400) -> ClientError:
    # botocore's stubs demand a complete ResponseMetadata block; a real error from
    # the service carries one, and a test that spelled out four unused HTTP fields
    # would be asserting against the stub rather than against the classifier.
    response: Any = {
        "Error": {"Code": code, "Message": "x"},
        "ResponseMetadata": {"HTTPStatusCode": status},
    }
    return ClientError(response, "Converse")


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("ValidationException", ModelErrorCode.VALIDATION_ERROR),
        ("AccessDeniedException", ModelErrorCode.ACCESS_DENIED),
        ("ExpiredTokenException", ModelErrorCode.ACCESS_DENIED),
        ("ThrottlingException", ModelErrorCode.THROTTLING),
        ("TooManyRequestsException", ModelErrorCode.THROTTLING),
        ("ServiceQuotaExceededException", ModelErrorCode.THROTTLING),
        ("ModelTimeoutException", ModelErrorCode.MODEL_TIMEOUT),
        ("InternalServerException", ModelErrorCode.TRANSIENT_SERVER_ERROR),
        ("ServiceUnavailableException", ModelErrorCode.TRANSIENT_SERVER_ERROR),
        ("ModelNotReadyException", ModelErrorCode.TRANSIENT_SERVER_ERROR),
        ("ResourceNotFoundException", ModelErrorCode.UNSUPPORTED_MODEL),
    ],
)
def test_every_bedrock_error_code_has_a_classification(code: str, expected: ModelErrorCode):
    assert classify(client_error(code)) is expected


def test_an_unknown_server_error_is_treated_as_transient():
    assert (
        classify(client_error("SomethingNew", status=503)) is ModelErrorCode.TRANSIENT_SERVER_ERROR
    )


def test_an_unknown_client_error_is_not_retried():
    """Defaulting an unrecognised 4xx to transient would spend the budget re-failing."""
    assert classify(client_error("SomethingNew", status=400)) is ModelErrorCode.VALIDATION_ERROR


def test_transport_and_sdk_failures_are_classified():
    assert classify(ReadTimeoutError(endpoint_url="https://x")) is ModelErrorCode.MODEL_TIMEOUT
    assert classify(ModelThrottledException("slow down")) is ModelErrorCode.THROTTLING
    assert (
        classify(ContextWindowOverflowException("too long")) is ModelErrorCode.TOKEN_LIMIT_EXCEEDED
    )
    assert classify(MaxTokensReachedException("cut off")) is ModelErrorCode.TOKEN_LIMIT_EXCEEDED
    assert (
        classify(StructuredOutputException("no value"))
        is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    )


def test_a_schema_violation_is_classified_as_repairable():
    class Strict(BaseModel):
        value: int

    try:
        Strict.model_validate({"value": "not a number"})
    except ValidationError as exc:
        assert classify(exc) is ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT
    else:  # pragma: no cover - the model above cannot accept that value
        pytest.fail("expected a validation error")


def test_an_unrecognised_exception_is_not_given_a_plausible_code():
    assert classify(RuntimeError("something else entirely")) is None


@pytest.mark.parametrize(
    ("code", "retryable"),
    [
        (ModelErrorCode.THROTTLING, True),
        (ModelErrorCode.MODEL_TIMEOUT, True),
        (ModelErrorCode.TRANSIENT_SERVER_ERROR, True),
        (ModelErrorCode.MALFORMED_STRUCTURED_OUTPUT, True),
        (ModelErrorCode.VALIDATION_ERROR, False),
        (ModelErrorCode.ACCESS_DENIED, False),
        (ModelErrorCode.UNSUPPORTED_MODEL, False),
        (ModelErrorCode.TOKEN_LIMIT_EXCEEDED, False),
    ],
)
def test_only_transient_and_repairable_failures_are_retried(code: ModelErrorCode, retryable: bool):
    assert is_retryable(RuntimeError("x"), code) is retryable


def test_a_repair_is_retried_even_under_a_terminal_code():
    """An over-long summary is a token-limit failure the next attempt can be told about."""
    repair = SchemaRepairNeeded("write less", code=ModelErrorCode.TOKEN_LIMIT_EXCEEDED)

    assert is_retryable(repair, repair.code) is True


def retrier(attempts: int) -> tuple[Retrier, list[float]]:
    waits: list[float] = []
    return (
        Retrier(
            policy=RetryPolicy(max_attempts=attempts),
            sleep=waits.append,
            rng=random.Random(7),
        ),
        waits,
    )


def test_a_call_that_works_first_time_records_no_retries():
    driver, waits = retrier(4)

    value, retries = driver.run(lambda _index, _hint: "done")

    assert (value, retries) == ("done", 0)
    assert waits == []


def test_retries_are_bounded_by_the_configured_attempt_count():
    driver, waits = retrier(3)
    calls: list[int] = []

    def always_throttled(index: int, _hint: str | None) -> str:
        calls.append(index)
        raise client_error("ThrottlingException")

    with pytest.raises(RetriesExhausted) as excinfo:
        driver.run(always_throttled)

    assert calls == [0, 1, 2]
    assert excinfo.value.attempts == 3
    assert excinfo.value.code is ModelErrorCode.THROTTLING
    assert len(waits) == 2, "the last failure is not followed by a wait"


def test_a_failure_that_cannot_be_repaired_is_not_attempted_twice():
    driver, waits = retrier(4)
    calls: list[int] = []

    def denied(index: int, _hint: str | None) -> str:
        calls.append(index)
        raise client_error("AccessDeniedException", status=403)

    with pytest.raises(RetriesExhausted) as excinfo:
        driver.run(denied)

    assert calls == [0]
    assert excinfo.value.code is ModelErrorCode.ACCESS_DENIED
    assert waits == []


def test_a_repair_hint_reaches_the_next_attempt():
    driver, _ = retrier(3)
    seen: list[str | None] = []

    def repairing(index: int, hint: str | None) -> str:
        seen.append(hint)
        if index == 0:
            raise SchemaRepairNeeded("write less", code=ModelErrorCode.TOKEN_LIMIT_EXCEEDED)
        return "shorter"

    value, retries = driver.run(repairing)

    assert (value, retries) == ("shorter", 1)
    assert seen == [None, "write less"]


def test_an_unclassifiable_failure_propagates_unchanged():
    driver, _ = retrier(4)

    def broken(_index: int, _hint: str | None) -> str:
        raise ZeroDivisionError("a bug, not a provider problem")

    with pytest.raises(ZeroDivisionError):
        driver.run(broken)


def test_backoff_grows_and_is_bounded():
    policy = RetryPolicy(max_attempts=8, base_delay_seconds=0.5, max_delay_seconds=4.0)

    full = [policy.delay_for(attempt, 1.0) for attempt in range(6)]

    assert full == [0.5, 1.0, 2.0, 4.0, 4.0, 4.0]
    assert policy.delay_for(0, 0.0) == 0.0, "full jitter can wait no time at all"
    assert 0.0 <= policy.delay_for(3, 0.25) <= 4.0


def test_a_connection_failure_is_treated_as_transient():
    from botocore.exceptions import EndpointConnectionError

    error = EndpointConnectionError(endpoint_url="https://bedrock-runtime.eu-west-2.amazonaws.com")

    assert classify(error) is ModelErrorCode.TRANSIENT_SERVER_ERROR


def test_a_policy_that_permits_no_attempt_at_all_is_a_bug_and_says_so():
    """``max_model_retries`` cannot produce this, but a hand-built policy can."""
    driver = Retrier(policy=RetryPolicy(max_attempts=0), sleep=lambda _s: None)

    with pytest.raises(AssertionError, match="unreachable"):
        driver.run(lambda _index, _hint: "never called")
