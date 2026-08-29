"""Embeddings are typed, deduplicated, and never store the text they came from."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from attention_sink.domain import content_hash
from attention_sink.model_gateway import (
    SUPPORTED_DIMENSIONS,
    BedrockEmbeddingProvider,
    FixtureEmbeddingProvider,
    ModelErrorCode,
    ModelInvocationError,
    ModelRole,
    Retrier,
    RetryPolicy,
)
from tests.doubles import FakeRuntime
from tests.unit.test_failures import client_error

FIXED_TIME = datetime(2026, 8, 29, 12, tzinfo=UTC)


def provider(runtime: FakeRuntime, *, dimensions: int = 256) -> BedrockEmbeddingProvider:
    return BedrockEmbeddingProvider(
        model_id="embedding-model",
        region="eu-west-2",
        client=runtime,
        dimensions=dimensions,
        retrier=Retrier(policy=RetryPolicy(max_attempts=2), sleep=lambda _s: None),
        now=lambda: FIXED_TIME,
    )


def test_a_record_carries_its_model_dimensions_hash_and_time():
    runtime = FakeRuntime(vectors=[[0.5] * 256])

    result = provider(runtime).embed("a memory worth finding again")

    record = result.record
    assert record.model_id == "embedding-model"
    assert record.dimensions == 256
    assert record.input_hash == content_hash("a memory worth finding again")
    assert len(record.vector) == 256
    assert record.normalized is True
    assert record.created_at == FIXED_TIME
    assert result.metadata.role is ModelRole.EMBEDDING
    assert result.metadata.request_id == "embed-0"


def test_the_text_itself_is_not_stored():
    runtime = FakeRuntime(vectors=[[0.5] * 256])

    record = provider(runtime).embed("a private recollection").record

    assert "a private recollection" not in record.model_dump_json()


def test_normalisation_is_asked_of_the_model_not_applied_afterwards():
    runtime = FakeRuntime(vectors=[[0.5] * 256])

    provider(runtime).embed("text")

    assert runtime.invoke_requests[0]["body"] == {
        "inputText": "text",
        "dimensions": 256,
        "normalize": True,
    }


def test_the_same_text_is_embedded_once_per_model():
    runtime = FakeRuntime(vectors=[[0.5] * 256, [0.9] * 256])
    subject = provider(runtime)

    first = subject.embed("the same words")
    again = subject.embed("the same words")
    other = subject.embed("different words")

    assert first.deduplicated is False
    assert again.deduplicated is True
    assert again.record == first.record
    assert other.deduplicated is False
    assert len(runtime.invoke_requests) == 2
    assert subject.cached_count == 2


def test_a_cache_key_is_the_model_and_the_content_hash():
    runtime = FakeRuntime(vectors=[[0.5] * 256])

    record = provider(runtime).embed("text").record

    assert record.cache_key == ("embedding-model", content_hash("text"))


def test_a_dimension_the_model_does_not_offer_is_refused_at_construction():
    with pytest.raises(ValueError, match="dimensions must be one of"):
        provider(FakeRuntime(), dimensions=300)

    assert 1024 in SUPPORTED_DIMENSIONS


def test_a_vector_that_does_not_match_the_declared_size_is_refused():
    runtime = FakeRuntime(vectors=[[0.5] * 12])

    with pytest.raises(ValueError, match="declares 256 dimensions"):
        provider(runtime).embed("text")


def test_empty_text_is_refused_rather_than_embedded_as_zero():
    runtime = FakeRuntime(vectors=[[0.5] * 256])

    with pytest.raises(ValueError, match="empty text"):
        provider(runtime).embed("   ")

    assert runtime.invoke_requests == []


def test_a_failed_embedding_reports_its_terminal_code():
    runtime = FakeRuntime(vectors=[client_error("ResourceNotFoundException", status=404)])

    with pytest.raises(ModelInvocationError) as excinfo:
        provider(runtime).embed("text")

    assert excinfo.value.code is ModelErrorCode.UNSUPPORTED_MODEL
    assert excinfo.value.metadata.retry_count == 0


def test_fixture_vectors_are_deterministic_normalised_and_marked():
    first = FixtureEmbeddingProvider(now=lambda: FIXED_TIME).embed("a memory")
    second = FixtureEmbeddingProvider(now=lambda: FIXED_TIME).embed("a memory")

    assert first.record == second.record
    assert first.metadata.simulated is True
    assert abs(sum(value * value for value in first.record.vector) - 1.0) < 1e-9
