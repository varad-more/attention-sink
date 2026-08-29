"""Titan Text Embeddings V2 through Bedrock, deduplicated by content.

An embedding is a pure function of its model and its input, so the same text
embedded twice by the same model is one record and not two. That identity --
``(model_id, input_hash)`` -- is enforced here, in the only place embeddings are
produced, rather than left to whichever store happens to write them.

The text itself is not kept on the record. A vector plus a content hash is enough to
find the memory it belongs to, and the memory already holds the text.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from time import monotonic
from typing import Any

from attention_sink.domain import content_hash
from attention_sink.model_gateway.failures import (
    ModelInvocationError,
    Retrier,
    RetriesExhausted,
)
from attention_sink.model_gateway.interfaces import BedrockRuntimeApi, EmbeddingResult
from attention_sink.model_gateway.observability import (
    CallMetadata,
    CallOutcome,
    ModelErrorCode,
    ModelRole,
    request_id_of,
)
from attention_sink.model_gateway.schemas import EmbeddingRecord

__all__ = ["SUPPORTED_DIMENSIONS", "BedrockEmbeddingProvider"]

SUPPORTED_DIMENSIONS: tuple[int, ...] = (256, 512, 1024)
"""Output sizes Titan Text Embeddings V2 accepts.

Checked at construction rather than at the first call. A dimension the model will
reject is a configuration mistake, and it should stop the process that made it
rather than the run that inherits it.
"""


@dataclass
class BedrockEmbeddingProvider:
    """Embeds text once per model identifier and content hash.

    Normalisation is requested from the model rather than applied afterwards. Titan
    V2 exposes it directly, and a vector normalised by the provider is the vector
    the provider's own similarity guidance assumes.
    """

    model_id: str
    region: str
    client: BedrockRuntimeApi
    dimensions: int = 1024
    normalize: bool = True
    retrier: Retrier = field(default_factory=Retrier)
    clock: Callable[[], float] = monotonic
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    _cache: dict[tuple[str, str], EmbeddingRecord] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        """Reject an output size the model does not offer.

        Raises:
            ValueError: ``dimensions`` is not one of :data:`SUPPORTED_DIMENSIONS`.
        """
        if self.dimensions not in SUPPORTED_DIMENSIONS:
            msg = f"dimensions must be one of {SUPPORTED_DIMENSIONS}, got {self.dimensions}"
            raise ValueError(msg)

    @property
    def cached_count(self) -> int:
        """How many distinct texts this provider has embedded."""
        return len(self._cache)

    def embed(self, text: str) -> EmbeddingResult:
        """Embed ``text``, returning the existing record when there is one.

        Raises:
            ValueError: ``text`` is empty or whitespace. There is nothing to embed,
                and a zero vector would be a fabricated answer rather than a null one.
            ModelInvocationError: Every permitted attempt failed.
        """
        if not text.strip():
            msg = "refusing to embed empty text"
            raise ValueError(msg)

        input_hash = content_hash(text)
        cache_key = (self.model_id, input_hash)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return EmbeddingResult(
                record=cached,
                deduplicated=True,
                metadata=self._metadata(CallOutcome.SUCCESS, 0, 0),
            )

        body = json.dumps(
            {"inputText": text, "dimensions": self.dimensions, "normalize": self.normalize}
        )
        started = self.clock()

        def attempt(_index: int, _hint: str | None) -> Any:
            return self.client.invoke_model(
                modelId=self.model_id,
                body=body,
                accept="application/json",
                contentType="application/json",
            )

        try:
            response, retries = self.retrier.run(attempt)
        except RetriesExhausted as exc:
            metadata = self._metadata(
                CallOutcome.FAILURE,
                self._elapsed_ms(started),
                max(exc.attempts - 1, 0),
                error_code=exc.code,
            )
            msg = f"embedding failed as {exc.code.value} using {self.model_id}"
            raise ModelInvocationError(msg, metadata=metadata) from exc

        payload = json.loads(response["body"].read())
        record = EmbeddingRecord(
            model_id=self.model_id,
            dimensions=self.dimensions,
            input_hash=input_hash,
            vector=tuple(float(value) for value in payload["embedding"]),
            normalized=self.normalize,
            created_at=self.now(),
        )
        self._cache[cache_key] = record
        return EmbeddingResult(
            record=record,
            deduplicated=False,
            metadata=self._metadata(
                CallOutcome.SUCCESS,
                self._elapsed_ms(started),
                retries,
                input_tokens=payload.get("inputTextTokenCount"),
                request_id=request_id_of(response),
            ),
        )

    def _elapsed_ms(self, started: float) -> int:
        return max(int((self.clock() - started) * 1000), 0)

    def _metadata(
        self,
        outcome: CallOutcome,
        latency_ms: int,
        retry_count: int,
        *,
        error_code: ModelErrorCode | None = None,
        input_tokens: int | None = None,
        request_id: str | None = None,
    ) -> CallMetadata:
        return CallMetadata(
            role=ModelRole.EMBEDDING,
            model_id=self.model_id,
            region=self.region,
            outcome=outcome,
            latency_ms=latency_ms,
            retry_count=retry_count,
            simulated=False,
            input_tokens=input_tokens,
            request_id=request_id,
            error_code=error_code,
        )
