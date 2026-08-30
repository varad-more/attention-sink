"""Exact token counting through Bedrock's CountTokens API.

ADR-008 denominates the active-memory budget in versioned budget tokens and warns
that a heuristic unit must never be described as though it were the model's. ADR-011
resolves that for production: a real run counts with the tokeniser of the model that
will read the text, and the heuristic counter remains only where a test needs a
number without a network.

Two counters satisfy that. :class:`BedrockTokenCounter` asks the provider's
``CountTokens`` operation. :class:`ConverseTokenCounter` asks the model itself,
through a ``Converse`` request capped at one output token, and reads the exact input
total the provider reports back. Both are the writer model's own tokenisation of the
exact text; they differ in which operation reports it, and the second exists because
no model this deployment can reach supports the first (ADR-013).

There is no fallback. A production process whose counter is unavailable stops. Silent
degradation to an approximation would leave every arm in that run measured against a
budget in a different unit from the one its manifest claims, and nothing downstream
would show it.

:class:`ApproximateTokenCounter` is the other counter, and it is not a fallback. It is
selected by configuration, records its own version on every budget it measures, and is
refused for a canonical run (ADR-012). The difference between "configured" and "fallen
back to" is the whole of the guarantee: one is a decision recorded in a manifest, the
other is a silent change of unit nobody would see.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from time import monotonic
from typing import TYPE_CHECKING, Any, ClassVar

from attention_sink.domain import HeuristicTokenCounter, content_hash
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
    from mypy_boto3_bedrock_runtime.type_defs import (
        CountTokensInputTypeDef,
        InferenceConfigurationTypeDef,
        MessageTypeDef,
    )

__all__ = [
    "BEDROCK_COUNTER_VERSION",
    "CONVERSE_COUNTER_VERSION",
    "ApproximateTokenCounter",
    "BedrockTokenCounter",
    "ConverseTokenCounter",
]

CONVERSE_COUNTER_VERSION = "bedrock-converse-usage-v1"
"""Counter version recorded on every ``TokenBudget`` counted by :class:`ConverseTokenCounter`.

Distinct from :data:`BEDROCK_COUNTER_VERSION` because the two are distinct
measurements even when they agree: they ask different operations, and a run
denominated in one must be legible as such next to a run denominated in the other.
"""

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

    output_tokens_per_call: ClassVar[int] = 0
    """What one count costs in generated tokens. ``CountTokens`` generates nothing."""

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
        return self._count(key=text, system=None, user=text)

    def count_request(self, *, system: str, user: str) -> TokenCount:
        """Count a complete two-turn request exactly as it will be sent.

        This is the number a production budget is held against: the memories, the
        instructions that surround them, and the structure the provider adds.

        Raises:
            ModelInvocationError: Every permitted attempt failed.
        """
        return self._count(key=f"{system}\n\n{user}", system=system, user=user)

    def _invoke(self, *, system: str | None, user: str) -> Mapping[str, Any]:
        """Ask the provider what this request costs, and return its raw answer."""
        messages: list[MessageTypeDef] = [_user_turn(user)]
        payload: CountTokensInputTypeDef = (
            {"converse": {"messages": messages, "system": [{"text": system}]}}
            if system is not None
            else {"converse": {"messages": messages}}
        )
        return self.client.count_tokens(modelId=self.model_id, input=payload)

    @staticmethod
    def _input_tokens_of(response: Mapping[str, Any]) -> int:
        """Read the input total out of one provider answer."""
        return int(response["inputTokens"])

    def _count(self, *, key: str, system: str | None, user: str) -> TokenCount:
        cache_key = (self.model_id, content_hash(key))
        cached = self._cache.get(cache_key)
        if cached is not None:
            # The count is the cached one; the *cost* is zero, because no request was
            # made. A run's token tally adds these up, and a cache hit that reported
            # the tokens it did not spend would make the tally describe a bill nobody
            # was sent.
            return TokenCount(tokens=cached, metadata=self._metadata(CallOutcome.SUCCESS, 0, 0, 0))

        started = self.clock()

        def attempt(_index: int, _hint: str | None) -> Any:
            return self._invoke(system=system, user=user)

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

        tokens = self._input_tokens_of(response)
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
            output_tokens=self.output_tokens_per_call if input_tokens else 0,
            request_id=request_id,
            error_code=error_code,
        )


@dataclass
class ConverseTokenCounter(BedrockTokenCounter):
    """Counts by asking the model, when the provider will not answer separately.

    ``CountTokens`` is the operation built for this, and no model this deployment can
    reach supports it -- every one answers ``ValidationException: The provided model
    doesn't support counting tokens``. A ``Converse`` request capped at a single
    output token gets the same number by a different route: the provider reports
    ``usage.inputTokens`` for every invocation, and that total is the writer model's
    own tokenisation of exactly the text it was given.

    This is an exact counter, not an approximation, and that is the whole point of it
    (ADR-013). It differs from :class:`BedrockTokenCounter` in cost rather than in
    kind: a count is a billed invocation, so counting calls are claimed against the
    run's model-call ceiling like any other call.
    """

    version: str = CONVERSE_COUNTER_VERSION

    output_tokens_per_call: ClassVar[int] = 1
    """One. The cap cannot be zero, so every count generates a single token."""

    def _invoke(self, *, system: str | None, user: str) -> Mapping[str, Any]:
        """Invoke the model with the output capped as low as the provider allows."""
        inference: InferenceConfigurationTypeDef = {"maxTokens": 1, "temperature": 0.0}
        if system is None:
            return self.client.converse(
                modelId=self.model_id,
                messages=[_user_turn(user)],
                inferenceConfig=inference,
            )
        return self.client.converse(
            modelId=self.model_id,
            messages=[_user_turn(user)],
            system=[{"text": system}],
            inferenceConfig=inference,
        )

    @staticmethod
    def _input_tokens_of(response: Mapping[str, Any]) -> int:
        """Read the input total off the invocation's usage report."""
        return int(response["usage"]["inputTokens"])


def _user_turn(text: str) -> MessageTypeDef:
    """One user message in the shape both counting operations expect."""
    return {"role": "user", "content": [{"text": text}]}


@dataclass(frozen=True, slots=True)
class ApproximateTokenCounter:
    """The heuristic counter, wearing the exact counter's interface.

    Two situations reach it, and both are chosen rather than fallen into. Fixture mode
    calls no model at all, so there is no tokeniser to ask. And a deployment whose
    Region offers no model supporting Bedrock ``CountTokens`` may record that it used
    this one instead (ADR-012) -- explicitly, in configuration, and never for a
    canonical run.

    :attr:`version` is ``heuristic-v1`` either way, and it travels on every
    ``TokenBudget`` this counter measures. A run counted this way is visibly not a run
    counted against a model's own tokeniser, which is the property that stops the two
    from ever being compared as though they were the same measurement.
    """

    model_id: str
    region: str
    simulated: bool
    """Whether the surrounding gateway fabricates its generations.

    A property of the run, not of the counter: an approximate count in a staging run
    against real models is an approximation of something real.
    """

    counter: HeuristicTokenCounter = field(default_factory=HeuristicTokenCounter)

    @property
    def version(self) -> str:
        """The heuristic counter's version, unchanged."""
        return self.counter.version

    def count(self, text: str) -> int:
        """Return the budget-token cost of ``text``."""
        return self.counter.count(text)

    def count_detailed(self, text: str) -> TokenCount:
        """Count ``text`` and report the call that was not made."""
        tokens = self.counter.count(text)
        return TokenCount(tokens=tokens, metadata=self._metadata(tokens))

    def count_request(self, *, system: str, user: str) -> TokenCount:
        """Count both turns as one block."""
        return self.count_detailed(f"{system}\n\n{user}")

    def _metadata(self, tokens: int) -> CallMetadata:
        return CallMetadata(
            role=ModelRole.TOKEN_COUNTER,
            model_id=self.model_id,
            region=self.region,
            outcome=CallOutcome.SUCCESS,
            latency_ms=0,
            retry_count=0,
            simulated=self.simulated,
            input_tokens=tokens,
        )
