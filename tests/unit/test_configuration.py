"""Run configuration, and the checks that run before a single model call is made."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from attention_sink.domain import (
    ArmId,
    InferenceParameters,
    Memory,
    MemoryKind,
    ModelConfiguration,
    PinnedOriginConfig,
    PolicyConfiguration,
    PolicyError,
    RunConfiguration,
    UnsatisfiableBudgetError,
)
from tests.factories import PROMPT_VERSION, PROTOCOL_VERSION, RUN_ID, RUN_SEED, budget

PINNED = "mem_arm_sink_000000"


def a_run(max_tokens: int = 100, **changes: object) -> RunConfiguration:
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model_configuration": ModelConfiguration(
            writer_model_id="writer",
            auditor_model_id="auditor",
            judge_model_id="judge",
            summary_model_id="summary",
            embedding_model_id="embedding",
            inference=InferenceParameters(temperature=0.7, top_p=0.9, max_output_tokens=512),
        ),
        "budget": budget(max_tokens),
        "random_seed": RUN_SEED,
        "created_at": datetime.now(UTC),
    }
    return RunConfiguration(**{**base, **changes})  # type: ignore[arg-type]


def a_seed(memory_id: str, tokens: int, kind: MemoryKind = MemoryKind.SEED) -> Memory:
    return Memory(
        memory_id=memory_id,
        run_id=RUN_ID,
        arm_id=ArmId.ARM_SINK,
        text=f"seed memory {memory_id}",
        token_count=tokens,
        memory_kind=kind,
        birth_cycle=0,
        creation_sequence=int(memory_id[-6:]),
    )


def test_a_seed_set_within_budget_validates() -> None:
    a_run().validate_seed_memories([a_seed(PINNED, 20), a_seed("mem_arm_sink_000001", 20)])


def test_a_seed_set_over_budget_is_rejected_before_the_run_starts() -> None:
    with pytest.raises(UnsatisfiableBudgetError, match="every arm starts from"):
        a_run(50).validate_seed_memories([a_seed(PINNED, 40), a_seed("mem_arm_sink_000001", 40)])


def test_a_missing_pinned_memory_is_rejected() -> None:
    run = a_run(
        policies=PolicyConfiguration(pinned_origin=PinnedOriginConfig(pinned_memory_id=PINNED))
    )
    with pytest.raises(PolicyError, match="not present in the seed set"):
        run.validate_seed_memories([a_seed("mem_arm_sink_000001", 10)])


def test_a_pinned_memory_that_is_not_a_seed_is_rejected() -> None:
    run = a_run(
        policies=PolicyConfiguration(pinned_origin=PinnedOriginConfig(pinned_memory_id=PINNED))
    )
    with pytest.raises(PolicyError, match="not a seed"):
        run.validate_seed_memories([a_seed(PINNED, 10, kind=MemoryKind.GENERATED)])


def test_a_pinned_memory_larger_than_the_budget_is_rejected() -> None:
    run = a_run(
        50, policies=PolicyConfiguration(pinned_origin=PinnedOriginConfig(pinned_memory_id=PINNED))
    )
    with pytest.raises(UnsatisfiableBudgetError, match="on its own"):
        run.validate_seed_memories([a_seed(PINNED, 60)])


def test_no_pin_configured_skips_the_pin_checks() -> None:
    a_run().validate_seed_memories([a_seed("mem_arm_sink_000001", 10)])


def test_a_run_needs_at_least_one_arm_and_no_duplicates() -> None:
    with pytest.raises(ValidationError, match="at least one arm"):
        a_run(arms=())
    with pytest.raises(ValidationError, match="same arm twice"):
        a_run(arms=(ArmId.ARM_FIFO, ArmId.ARM_FIFO))


def test_a_naive_timestamp_is_rejected() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        a_run(created_at=datetime(2026, 8, 29, 12, 0, 0))


def test_the_decay_is_bounded_to_a_discount() -> None:
    with pytest.raises(ValidationError):
        a_run(citation_decay=1.5)
    with pytest.raises(ValidationError):
        a_run(citation_decay=-0.1)


def test_a_short_random_seed_is_rejected() -> None:
    with pytest.raises(ValidationError):
        a_run(random_seed="short")


def test_a_run_configuration_round_trips() -> None:
    run = a_run()
    assert RunConfiguration.model_validate_json(run.model_dump_json()) == run
