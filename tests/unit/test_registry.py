"""Arm resolution: the only path from an identifier to a mechanism."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from attention_sink.domain import (
    CANONICAL_ARMS,
    DEFAULT_RECENCY_RESERVE,
    REFERENCE_ARMS,
    ArmId,
    HeavyHitterConfig,
    InferenceParameters,
    ModelConfiguration,
    PinnedOriginConfig,
    PolicyConfiguration,
    RunConfiguration,
    SummarizationConfig,
)
from attention_sink.policies import (
    DEFAULT_POLICIES,
    CitationWeightPolicy,
    PinnedOriginPolicy,
    SummarizationPolicy,
    canonical_policies,
    policies_for,
    policy_for,
)
from tests.factories import PROMPT_VERSION, PROTOCOL_VERSION, RUN_ID, RUN_SEED, budget


def a_run(arms: tuple[ArmId, ...] = CANONICAL_ARMS, **changes: object) -> RunConfiguration:
    return RunConfiguration(
        run_id=RUN_ID,
        protocol_version=PROTOCOL_VERSION,
        prompt_version=PROMPT_VERSION,
        model_configuration=ModelConfiguration(
            writer_model_id="writer",
            auditor_model_id="auditor",
            judge_model_id="judge",
            summary_model_id="summary",
            embedding_model_id="embedding",
            inference=InferenceParameters(temperature=0.7, top_p=0.9, max_output_tokens=512),
        ),
        budget=budget(1000),
        random_seed=RUN_SEED,
        arms=arms,
        created_at=datetime.now(UTC),
        **changes,  # type: ignore[arg-type]
    )


def test_every_arm_resolves_to_its_own_mechanism() -> None:
    for arm in (*CANONICAL_ARMS, *REFERENCE_ARMS):
        assert policy_for(arm).arm_id is arm


def test_an_unregistered_arm_is_a_configuration_bug() -> None:
    registry = policies_for(PolicyConfiguration())
    assert set(registry) == set(CANONICAL_ARMS) | set(REFERENCE_ARMS)
    with pytest.raises(KeyError):
        registry["arm_invented"]  # type: ignore[index]


def test_configuration_reaches_the_policies_that_need_it() -> None:
    config = PolicyConfiguration(
        heavy_hitter=HeavyHitterConfig(recency_reserve=5),
        pinned_origin=PinnedOriginConfig(pinned_memory_id="mem_arm_sink_000000"),
        summarization=SummarizationConfig(summary_target_token_limit=12),
    )
    registry = policies_for(config)
    heavy = registry[ArmId.ARM_HEAVY]
    sink = registry[ArmId.ARM_SINK]
    summary = registry[ArmId.ARM_SUMMARY]
    assert isinstance(heavy, CitationWeightPolicy)
    assert isinstance(sink, PinnedOriginPolicy)
    assert isinstance(summary, SummarizationPolicy)
    assert heavy.config.recency_reserve == 5
    assert sink.config.pinned_memory_id == "mem_arm_sink_000000"
    assert summary.config.summary_target_token_limit == 12


def test_the_default_registry_uses_default_configuration() -> None:
    heavy = DEFAULT_POLICIES[ArmId.ARM_HEAVY]
    assert isinstance(heavy, CitationWeightPolicy)
    assert heavy.config.recency_reserve == DEFAULT_RECENCY_RESERVE


def test_canonical_policies_follow_a_fixed_order() -> None:
    ordered = canonical_policies(a_run())
    assert tuple(p.arm_id for p in ordered) == CANONICAL_ARMS


def test_canonical_policies_include_only_the_configured_arms() -> None:
    ordered = canonical_policies(a_run(arms=(ArmId.ARM_FIFO, ArmId.ARM_FULL)))
    assert tuple(p.arm_id for p in ordered) == (ArmId.ARM_FIFO, ArmId.ARM_FULL)


def test_policy_configuration_round_trips() -> None:
    config = PolicyConfiguration(heavy_hitter=HeavyHitterConfig(recency_reserve=7))
    assert PolicyConfiguration.model_validate_json(config.model_dump_json()) == config
