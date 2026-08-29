r"""Contract tests against real Bedrock. Skipped unless explicitly enabled.

These spend money and need credentials, so they are opt-in and never part of an
ordinary run. Enable with::

    AS_BEDROCK_CONTRACT_TESTS=1 MODEL_MODE=bedrock AWS_REGION=... \\
        WRITER_MODEL_ID=... AUDITOR_MODEL_ID=... JUDGE_MODEL_ID=... \\
        SUMMARY_MODEL_ID=... EMBEDDING_MODEL_ID=... uv run pytest -m integration

What they check is the contract, not the content: that the configured models accept
the schemas this package sends, that a response validates, that the token counter
answers, and that the embedding model returns the dimensions it was asked for. They
assert nothing about what a model says, because that is not a contract.
"""

from __future__ import annotations

import os

import pytest

from attention_sink.model_gateway import (
    GatewaySettings,
    ModelGateway,
    ModelMode,
    ThoughtOutput,
    build_gateway,
)
from tests.factories import world_state

ENABLED = os.environ.get("AS_BEDROCK_CONTRACT_TESTS", "").strip() == "1"

pytestmark = pytest.mark.skipif(
    not ENABLED,
    reason="Bedrock contract tests are opt-in; set AS_BEDROCK_CONTRACT_TESTS=1 to run them",
)

STIMULUS = "A ship's bell rings out somewhere in the fog."


@pytest.fixture(scope="module")
def gateway() -> ModelGateway:
    settings = GatewaySettings.from_env()
    if settings.mode is not ModelMode.BEDROCK:
        pytest.fail("contract tests were enabled but MODEL_MODE is not bedrock")
    return build_gateway(settings)


def test_the_writer_model_accepts_the_thought_schema(gateway: ModelGateway):
    state = world_state(count=3)

    result = gateway.writer.write(
        cycle=1, stimulus_text=STIMULUS, active_memories=state.active_memories
    )

    assert isinstance(result.output, ThoughtOutput)
    assert result.output.journal_entry.strip()
    assert set(result.cited_memory_ids) <= set(state.active_memory_ids)
    assert result.metadata.simulated is False
    assert result.metadata.input_tokens is not None


def test_the_token_counter_answers_for_the_writer_model(gateway: ModelGateway):
    state = world_state(count=3)
    block = "\n".join(memory.text for memory in state.active_memories)

    count = gateway.token_counter.count_detailed(block)

    assert count.tokens > 0
    assert count.metadata.request_id is not None


def test_the_embedding_model_returns_the_dimensions_it_was_asked_for(gateway: ModelGateway):
    result = gateway.embeddings.embed("The lighthouse was lit each evening.")

    assert len(result.record.vector) == result.record.dimensions
    assert result.deduplicated is False
