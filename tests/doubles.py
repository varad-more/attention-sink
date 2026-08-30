"""Test doubles for the model gateway.

The gateway has exactly one seam to a provider, :class:`StructuredInvoker`, so a
double for that seam exercises everything above it: prompt rendering, the blindness
guard, response verification, the retry policy, and the metadata record. Nothing here
mocks a Strands or botocore internal, because a test that did would be asserting
against a library's shape rather than against this repository's behaviour.
"""

from __future__ import annotations

import io
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel

from attention_sink.model_gateway import (
    FixtureInvoker,
    GatewaySettings,
    ModelGateway,
    RawResponse,
    build_gateway,
)

Scripted = dict[str, Any] | BaseException | Callable[[str], dict[str, Any]]
"""One scripted turn: a response payload, an exception to raise, or a builder."""


@dataclass(frozen=True, slots=True)
class RecordedCall:
    """What an adapter actually sent."""

    model_id: str
    system: str
    user: str
    output_model: type[BaseModel]
    temperature: float
    top_p: float
    max_tokens: int


@dataclass
class ScriptedInvoker:
    """Answers each call from a script, repeating the last entry once exhausted.

    Repeating rather than running out is what lets a retry test say "this keeps
    failing" in one line instead of listing an entry per permitted attempt.
    """

    script: list[Scripted] = field(default_factory=list)
    calls: list[RecordedCall] = field(default_factory=list)

    def invoke[T: BaseModel](
        self,
        *,
        model_id: str,
        system: str,
        user: str,
        output_model: type[T],
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> RawResponse[T]:
        """Record the call and return, or raise, whatever the script says."""
        index = len(self.calls)
        self.calls.append(
            RecordedCall(
                model_id=model_id,
                system=system,
                user=user,
                output_model=output_model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
        )
        if not self.script:
            msg = "the invoker was called but nothing was scripted"
            raise AssertionError(msg)
        entry = self.script[min(index, len(self.script) - 1)]
        if isinstance(entry, BaseException):
            raise entry
        payload = entry(user) if callable(entry) else entry
        return RawResponse(
            output=output_model.model_validate(payload),
            stop_reason="end_turn",
            input_tokens=len(f"{system} {user}".split()),
            output_tokens=7,
        )


def scripted_gateway(
    *script: Scripted, retries: int = 2, **env: str
) -> tuple[ModelGateway, ScriptedInvoker]:
    """A fixture-mode gateway whose provider seam is the supplied script.

    Fixture mode because no AWS configuration should be needed to exercise adapter
    logic; the invoker is replaced either way, so the mode only decides what the
    metadata is marked as.
    """
    invoker = ScriptedInvoker(script=list(script))
    settings = GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": str(retries), **env})
    gateway = build_gateway(settings, invoker=invoker, sleep=lambda _seconds: None)
    return gateway, invoker


@dataclass
class FakeRuntime:
    """A ``bedrock-runtime`` stand-in covering the three operations this package calls.

    Deliberately not a mocked botocore client. The adapters are written against the
    narrow :class:`BedrockRuntimeApi` protocol, so a small object that answers those
    calls is a complete substitute, and a test that patched the SDK would be
    asserting against the SDK's shape instead.
    """

    token_counts: list[int | BaseException] = field(default_factory=list)
    vectors: list[list[float] | BaseException] = field(default_factory=list)
    count_requests: list[Mapping[str, Any]] = field(default_factory=list)
    invoke_requests: list[Mapping[str, Any]] = field(default_factory=list)
    converse_requests: list[Mapping[str, Any]] = field(default_factory=list)

    def count_tokens(self, *, modelId: str, input: Any) -> Mapping[str, Any]:
        """Return the next scripted count, repeating the last once exhausted."""
        index = len(self.count_requests)
        self.count_requests.append({"modelId": modelId, "input": input})
        entry = _next(self.token_counts, index, "token count")
        return {
            "inputTokens": entry,
            "ResponseMetadata": {"RequestId": f"count-{index}", "HTTPStatusCode": 200},
        }

    def converse(self, *, modelId: str, messages: Any, **rest: Any) -> Mapping[str, Any]:
        """Return the next scripted count as an invocation's usage report.

        Shares ``token_counts`` with :meth:`count_tokens` on purpose: the two counters
        are two routes to one number, and a test that scripted them separately could
        assert a difference the real service does not have.
        """
        index = len(self.converse_requests)
        self.converse_requests.append({"modelId": modelId, "messages": messages, **rest})
        entry = _next(self.token_counts, index, "token count")
        return {
            "output": {"message": {"role": "assistant", "content": [{"text": "."}]}},
            "stopReason": "max_tokens",
            "usage": {"inputTokens": entry, "outputTokens": 1, "totalTokens": entry + 1},
            "ResponseMetadata": {"RequestId": f"converse-{index}", "HTTPStatusCode": 200},
        }

    def invoke_model(
        self, *, modelId: str, body: str, accept: str, contentType: str
    ) -> Mapping[str, Any]:
        """Return the next scripted vector, repeating the last once exhausted."""
        index = len(self.invoke_requests)
        self.invoke_requests.append(
            {"modelId": modelId, "body": json.loads(body), "accept": accept, "type": contentType}
        )
        entry = _next(self.vectors, index, "embedding")
        payload = json.dumps({"embedding": entry, "inputTextTokenCount": 4}).encode()
        return {
            "body": io.BytesIO(payload),
            "ResponseMetadata": {"RequestId": f"embed-{index}", "HTTPStatusCode": 200},
        }


def _next[T](script: list[T | BaseException], index: int, what: str) -> T:
    """Take the scripted entry for ``index``, raising it if it is an exception."""
    if not script:
        msg = f"the runtime was asked for a {what} but nothing was scripted"
        raise AssertionError(msg)
    entry = script[min(index, len(script) - 1)]
    if isinstance(entry, BaseException):
        raise entry
    return entry


@dataclass
class RecordingInvoker:
    """Answers exactly as the fixture invoker does, and keeps every request.

    A wrapper rather than a second fake, so a test that inspects what reached a model
    is inspecting the same bytes a local run actually sends. Used by the blindness
    tests, which have to assert about the rendered prompt itself.
    """

    inner: FixtureInvoker = field(default_factory=FixtureInvoker)
    calls: list[RecordedCall] = field(default_factory=list)

    def invoke[T: BaseModel](
        self,
        *,
        model_id: str,
        system: str,
        user: str,
        output_model: type[T],
        temperature: float,
        top_p: float,
        max_tokens: int,
    ) -> RawResponse[T]:
        """Record the request, then answer it deterministically."""
        self.calls.append(
            RecordedCall(
                model_id=model_id,
                system=system,
                user=user,
                output_model=output_model,
                temperature=temperature,
                top_p=top_p,
                max_tokens=max_tokens,
            )
        )
        return self.inner.invoke(
            model_id=model_id,
            system=system,
            user=user,
            output_model=output_model,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
        )

    @property
    def texts(self) -> list[str]:
        """Both turns of every recorded request, concatenated."""
        return [f"{call.system}\n{call.user}" for call in self.calls]


def recording_gateway() -> tuple[ModelGateway, RecordingInvoker]:
    """A fixture-mode gateway that keeps every request it sends."""
    invoker = RecordingInvoker()
    gateway = build_gateway(GatewaySettings.from_env(env={}), invoker=invoker)
    return gateway, invoker
