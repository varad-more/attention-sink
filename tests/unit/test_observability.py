"""Every call reports itself, and reports nothing it should not."""

from __future__ import annotations

import pytest

from attention_sink.model_gateway import (
    CallMetadata,
    CallOutcome,
    ModelErrorCode,
    ModelRole,
    request_id_of,
)


def metadata(**overrides: object) -> CallMetadata:
    base: dict[str, object] = {
        "role": ModelRole.WRITER,
        "model_id": "writer-model",
        "region": "eu-west-2",
        "outcome": CallOutcome.SUCCESS,
        "latency_ms": 12,
        "retry_count": 0,
        "simulated": False,
    }
    return CallMetadata.model_validate(base | overrides)


def test_a_successful_call_carries_no_error_code():
    assert metadata().error_code is None


def test_a_failure_must_name_its_code():
    with pytest.raises(ValueError, match="names no error code"):
        metadata(outcome=CallOutcome.FAILURE)


def test_a_success_may_not_carry_an_error_code():
    with pytest.raises(ValueError, match="succeeded but names error"):
        metadata(error_code=ModelErrorCode.THROTTLING)


def test_metadata_round_trips_through_json():
    record = metadata(
        outcome=CallOutcome.FAILURE,
        error_code=ModelErrorCode.THROTTLING,
        retry_count=3,
        prompt_version="writer/v1",
        prompt_hash="sha256:abc",
        stop_reason="end_turn",
        input_tokens=100,
        output_tokens=20,
        request_id="req-1",
    )

    assert CallMetadata.model_validate_json(record.model_dump_json()) == record


def test_only_the_request_identifier_is_taken_from_a_provider_response():
    """The rest of that block is HTTP headers, and headers carry authorization."""
    response = {
        "inputTokens": 42,
        "ResponseMetadata": {
            "RequestId": "abc-123",
            "HTTPStatusCode": 200,
            "HTTPHeaders": {
                "authorization": "AWS4-HMAC-SHA256 Credential=AKIAEXAMPLE/20260829/...",
                "x-amz-security-token": "a session token",
                "content-type": "application/json",
            },
            "RetryAttempts": 0,
        },
    }

    request_id = request_id_of(response)

    assert request_id == "abc-123"
    record = metadata(request_id=request_id)
    serialised = record.model_dump_json()
    assert "AWS4-HMAC" not in serialised
    assert "a session token" not in serialised


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"ResponseMetadata": None},
        {"ResponseMetadata": {}},
        {"ResponseMetadata": {"RequestId": ""}},
    ],
)
def test_a_response_without_a_request_identifier_reports_none(response: dict[str, object]):
    assert request_id_of(response) is None


def test_a_supported_citation_must_quote_the_span_that_supports_it():
    """Support with no evidence would be an assertion, and the audit exists to avoid one."""
    from attention_sink.model_gateway import AuditedCitation, SupportLevel

    with pytest.raises(ValueError, match="quotes no memory span"):
        AuditedCitation(memory_ref="m1", support_level=SupportLevel.FULL)

    assert (
        AuditedCitation(memory_ref="m1", support_level=SupportLevel.NONE).memory_evidence_span == ""
    )
