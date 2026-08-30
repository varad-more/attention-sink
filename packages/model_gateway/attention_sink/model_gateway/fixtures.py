"""Deterministic stand-ins for a model, for tests and for local development.

Permitted by the project constitution only behind explicit local configuration, and
gated by :func:`attention_sink.model_gateway.factory.build_gateway`, which will not
assemble a fixture gateway for a production runtime.

These are not a second gateway. :class:`FixtureInvoker` substitutes for the one class
that speaks to a provider, and the ordinary role adapters run above it unchanged, so
a local cycle exercises the same prompt rendering, the same blindness guard, the same
label resolution, the same retry policy, and the same metadata as production. What
differs is only where the bytes come from.

Everything produced here is marked. Text carries ``[simulated]``, metadata carries
``simulated=True``, and the fixture evaluator returns the null verdict of its task
with a score of zero, so a fabricated judgement can never read as a finding.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel

from attention_sink.domain import HeuristicTokenCounter, content_hash
from attention_sink.model_gateway.adapters import RawResponse
from attention_sink.model_gateway.interfaces import EmbeddingResult
from attention_sink.model_gateway.observability import CallMetadata, CallOutcome, ModelRole
from attention_sink.model_gateway.rendering import (
    parse_claims,
    parse_memory_block,
    parse_questions,
)
from attention_sink.model_gateway.schemas import (
    EVALUATION_LABELS,
    AuditOutput,
    EmbeddingRecord,
    EvaluationOutput,
    EvaluationTask,
    InterviewOutput,
    SummaryOutput,
    ThoughtOutput,
)

__all__ = [
    "FIXTURE_MODEL_ID",
    "FIXTURE_REGION",
    "SIMULATED_PREFIX",
    "FixtureEmbeddingProvider",
    "FixtureInvoker",
    "FixtureUnavailableError",
]

FIXTURE_MODEL_ID = "fixture-model-v1"
FIXTURE_REGION = "local"
SIMULATED_PREFIX = "[simulated]"

_MAX_STATEMENT = 380
_TASK_LINE = re.compile(r"^Task: (.+)$", re.MULTILINE)
_LIMIT_LINE = re.compile(r"^Token limit for summary_text: ([0-9]+)$", re.MULTILINE)
_COUNTER = HeuristicTokenCounter()
_DISCLAIMER = f"{SIMULATED_PREFIX} no model produced this."


class FixtureUnavailableError(LookupError):
    """No deterministic response is defined for the requested schema."""


def _clip(text: str, limit: int = _MAX_STATEMENT) -> str:
    """Trim to a length the schemas accept, never to nothing."""
    trimmed = " ".join(text.split())[:limit].strip()
    return trimmed or SIMULATED_PREFIX


def _fit(text: str, token_limit: int) -> str:
    """Drop trailing words until the text fits ``token_limit`` budget tokens."""
    words = text.split()
    while words and _COUNTER.count(" ".join(words)) > token_limit:
        words.pop()
    return " ".join(words) or "none"


def _writer_response(user: str) -> dict[str, Any]:
    memories = parse_memory_block(user)
    digest = content_hash(user).removeprefix("sha256:")[:12]
    cited = memories[:2]
    spans = {ref: f"it rests on {ref}" for ref, _ in cited}
    entry = " ".join(
        [
            f"{SIMULATED_PREFIX} A deterministic entry for request {digest}.",
            f"{len(memories)} memories were in view.",
            *(f"Here {spans[ref]}." for ref, _ in cited),
        ]
    )
    return {
        "journal_entry": entry,
        "candidate_memory": f"{SIMULATED_PREFIX} request {digest} held {len(memories)} memories.",
        "claimed_citations": [
            {
                "memory_ref": ref,
                "supported_statement": _clip(text),
                "journal_span": spans[ref],
            }
            for ref, text in cited
        ],
        "explicit_belief_claims": [_clip(text) for _, text in cited],
        "uncertainty_notes": [_DISCLAIMER],
    }


def _auditor_response(user: str) -> dict[str, Any]:
    memories = dict(parse_memory_block(user))
    audited: list[dict[str, Any]] = []
    for ref, _statement, span in parse_claims(user):
        source = memories.get(ref, "")
        if not source.strip():
            audited.append({"memory_ref": ref, "support_level": "NONE", "memory_evidence_span": ""})
            continue
        audited.append(
            {
                "memory_ref": ref,
                "support_level": "FULL",
                # A prefix of the record, so the adapter's verbatim-evidence check
                # passes for the same reason a real audit's would.
                "memory_evidence_span": _clip(source, 200),
                "entry_evidence_span": _clip(span, 200),
            }
        )
    return {"audited_citations": audited, "unsupported_claims": []}


def _summarizer_response(user: str) -> dict[str, Any]:
    memories = parse_memory_block(user)
    match = _LIMIT_LINE.search(user)
    limit = int(match.group(1)) if match else 32
    refs = [ref for ref, _ in memories]
    body = f"{SIMULATED_PREFIX} a compressed account of {len(refs)} records."
    return {
        "summary_text": _fit(body, limit),
        "source_memory_refs": refs,
        "preserved_fact_statements": [_clip(text) for _, text in memories[:2]],
        "omitted_fact_statements": [_clip(text) for _, text in memories[2:4]],
        "uncertainty_statements": [_DISCLAIMER],
    }


def _interview_response(user: str) -> dict[str, Any]:
    refs = [ref for ref, _ in parse_memory_block(user)][:1]
    return {
        "answers": [
            {
                "question_id": question_id,
                "answer": f"{SIMULATED_PREFIX} a deterministic answer to {question_id}.",
                "cited_memory_refs": refs,
                "stated_uncertainty": _DISCLAIMER,
            }
            for question_id, _text in parse_questions(user)
        ]
    }


def _evaluation_response(user: str) -> dict[str, Any]:
    match = _TASK_LINE.search(user)
    if match is None:
        msg = "evaluation request names no task"
        raise FixtureUnavailableError(msg)
    task = EvaluationTask(match.group(1).strip())
    return {
        "task": task.value,
        # The last verdict of every task is its null one, and the score is zero. A
        # fixture must not be able to look like a result.
        "label": EVALUATION_LABELS[task][-1],
        "score": 0.0,
        "evidence_memory_refs": [],
        "supporting_excerpts": [_DISCLAIMER],
    }


_RESPONSES: Mapping[type[BaseModel], Callable[[str], dict[str, Any]]] = {
    ThoughtOutput: _writer_response,
    AuditOutput: _auditor_response,
    SummaryOutput: _summarizer_response,
    InterviewOutput: _interview_response,
    EvaluationOutput: _evaluation_response,
}


@dataclass(frozen=True, slots=True)
class FixtureInvoker:
    """Answers any structured request deterministically, without a network.

    The response is derived from the rendered request, so the same request always
    yields the same bytes and a local run is reproducible. It reads the request back
    with the readers in :mod:`attention_sink.model_gateway.rendering`, which is why a
    change to how a prompt is laid out cannot leave the fake answering the old one.
    """

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
        """Return the deterministic response for ``output_model``.

        Raises:
            FixtureUnavailableError: No response is defined for that schema.
        """
        del model_id, temperature, top_p, max_tokens  # recorded by the caller, unused here
        build = _RESPONSES.get(output_model)
        if build is None:
            msg = f"no fixture response is defined for {output_model.__name__}"
            raise FixtureUnavailableError(msg)
        output = output_model.model_validate(build(user))
        return RawResponse(
            output=output,
            stop_reason="end_turn",
            input_tokens=_COUNTER.count(f"{system}\n\n{user}"),
            output_tokens=_COUNTER.count(output.model_dump_json()),
        )


def _fixture_vector(text: str, dimensions: int) -> tuple[float, ...]:
    """Expand a digest of ``text`` into a stable vector of the requested size."""
    values: list[float] = []
    block = 0
    while len(values) < dimensions:
        digest = hashlib.sha256(f"{block}:{text}".encode()).digest()
        values.extend((byte - 127.5) / 127.5 for byte in digest)
        block += 1
    return tuple(values[:dimensions])


@dataclass
class FixtureEmbeddingProvider:
    """Deterministic vectors, deduplicated the same way real ones are.

    The vectors carry no meaning. They exist so that code which stores, deduplicates,
    or compares embeddings can be exercised locally, and any similarity computed from
    them is a property of SHA-256 rather than of the text.
    """

    model_id: str = FIXTURE_MODEL_ID
    region: str = FIXTURE_REGION
    dimensions: int = 256
    normalize: bool = True
    now: Callable[[], datetime] = lambda: datetime.now(UTC)
    _cache: dict[tuple[str, str], EmbeddingRecord] = field(default_factory=dict, repr=False)

    @property
    def cached_count(self) -> int:
        """How many distinct texts this provider has embedded."""
        return len(self._cache)

    def embed(self, text: str) -> EmbeddingResult:
        """Embed ``text``, returning the existing record when there is one.

        Raises:
            ValueError: ``text`` is empty or whitespace.
        """
        if not text.strip():
            msg = "refusing to embed empty text"
            raise ValueError(msg)
        input_hash = content_hash(text)
        key = (self.model_id, input_hash)
        cached = self._cache.get(key)
        if cached is not None:
            return EmbeddingResult(record=cached, deduplicated=True, metadata=self._metadata())

        vector = _fixture_vector(text, self.dimensions)
        if self.normalize:
            norm = math.sqrt(sum(value * value for value in vector)) or 1.0
            vector = tuple(value / norm for value in vector)
        record = EmbeddingRecord(
            model_id=self.model_id,
            dimensions=self.dimensions,
            input_hash=input_hash,
            vector=vector,
            normalized=self.normalize,
            created_at=self.now(),
        )
        self._cache[key] = record
        return EmbeddingResult(record=record, deduplicated=False, metadata=self._metadata())

    def _metadata(self) -> CallMetadata:
        return CallMetadata(
            role=ModelRole.EMBEDDING,
            model_id=self.model_id,
            region=self.region,
            outcome=CallOutcome.SUCCESS,
            latency_ms=0,
            retry_count=0,
            simulated=True,
        )
