"""Exact token counting through Bedrock's CountTokens API.

ADR-008 denominates the active-memory budget in versioned budget tokens and warns
that a heuristic unit must never be described as though it were the model's. ADR-011
resolves that for production: a real run counts with the tokeniser of the model that
will read the text, and the heuristic counter remains only where a test needs a
number without a network.

There is no fallback. A production process whose counter is unavailable stops. Silent
degradation to an approximation would leave every arm in that run measured against a
budget in a different unit from the one its manifest claims, and nothing downstream
would show it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Any

from attention_sink.domain import content_hash
from attention_sink.model_gateway.failures import (
    ModelInvocationError,
    Retrier,
    RetriesExhausted,
)
from attention_sink.model_gateway.interfaces import BedrockRuntimeApi, TokenCount
from attention_sink.model_gateway.observability import (
    CallMetadata,
    CallOutcome,
    ModelErrorCode,
    ModelRole,
    request_id_of,
)

if TYPE_CHECKING:  # pragma: no cover - imports exist for typing only
    from mypy_boto3_bedrock_runtime.type_defs import CountTokensInputTypeDef, MessageTypeDef

__all__ = ["BEDROCK_COUNTER_VERSION", "BedrockTokenCounter"]

BEDROCK_COUNTER_VERSION = "bedrock-count-tokens-v1"
"""Counter version recorded on every ``TokenBudget`` counted this way.

The model identifier is not folded into this string: it contains characters the
domain's ``Version`` alias rejects, and it is already recorded in the run manifest.
The pair -- this version and the manifest's model identifier -- is what identifies
the counting function. :attr:`BedrockTokenCounter.descriptor` renders both together
for anywhere a single human-readable string is wanted.
"""


@dataclass
class BedrockTokenCounter:
    """Counts tokens the way the model that will read them counts.

    Counts are cached on model identifier and content hash. The same block of active
    memory is counted once per cycle by the budget and again inside the writer
    request, and every arm re-counts memories that have not changed since the cycle
    before, so the cache removes a large majority of the calls without changing a
    single number.
    """

    model_id: str
    region: str
    client: BedrockRuntimeApi
    retrier: Retrier = field(default_factory=Retrier)
    clock: Callable[[], float] = monotonic
    version: str = BEDROCK_COUNTER_VERSION
    _cache: dict[tuple[str, str], int] = field(default_factory=dict, repr=False)

    @property
    def descriptor(self) -> str:
        """Counter version and model identifier, as one readable string."""
        return f"{self.version}+{self.model_id}"

    @property
    def cached_count(self) -> int:
        """How many distinct texts this counter has counted."""
        return len(self._cache)

    def count(self, text: str) -> int:
        """Return the exact token cost of ``text``.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """
        return self.count_detailed(text).tokens

    def count_detailed(self, text: str) -> TokenCount:
        """Count ``text`` as a single user turn, reporting the call.

        Blank text costs nothing and is answered without a call: the provider would
        reject an empty content block, and "no text costs no tokens" is not a fact
        worth a network round trip to establish.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """
        if not text.strip():
            return TokenCount(tokens=0, metadata=self._metadata(CallOutcome.SUCCESS, 0, 0, 0))
        return self._count(
            key=text,
            payload={"converse": {"messages": [_user_turn(text)]}},
        )

    def count_request(self, *, system: str, user: str) -> TokenCount:
        """Count a complete two-turn request exactly as it will be sent.

        This is the number a production budget is held against: the memories, the
        instructions that surround them, and the structure the provider adds.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """
        return self._count(
            key=f"{system}\n\n{user}",
            payload={"converse": {"messages": [_user_turn(user)], "system": [{"text": system}]}},
        )

    def _count(self, *, key: str, payload: CountTokensInputTypeDef) -> TokenCount:
        cache_key = (self.model_id, content_hash(key))
        cached = self._cache.get(cache_key)
        if cached is not None:
            return TokenCount(
                tokens=cached, metadata=self._metadata(CallOutcome.SUCCESS, 0, 0, cached)
            )

        started = self.clock()

        def attempt(_index: int, _hint: str | None) -> Any:
            return self.client.count_tokens(modelId=self.model_id, input=payload)

        try:
            response, retries = self.retrier.run(attempt)
        except RetriesExhausted as exc:
            metadata = self._metadata(
                CallOutcome.FAILURE,
                self._elapsed_ms(started),
                max(exc.attempts - 1, 0),
                None,
                error_code=exc.code,
            )
            msg = f"token count failed as {exc.code.value} using {self.model_id}"
            raise ModelInvocationError(msg, metadata=metadata) from exc

        tokens = int(response["inputTokens"])
        self._cache[cache_key] = tokens
        return TokenCount(
            tokens=tokens,
            metadata=self._metadata(
                CallOutcome.SUCCESS,
                self._elapsed_ms(started),
                retries,
                tokens,
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
        input_tokens: int | None,
        *,
        error_code: ModelErrorCode | None = None,
        request_id: str | None = None,
    ) -> CallMetadata:
        return CallMetadata(
            role=ModelRole.TOKEN_COUNTER,
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


def _user_turn(text: str) -> MessageTypeDef:
    """One user message in the shape CountTokens expects."""
    return {"role": "user", "content": [{"text": text}]}
