"""The run configuration derived from a protocol, and what it refuses to derive."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from attention_sink.domain import CANONICAL_ARMS, ArmId, make_memory_id
from attention_sink.model_gateway import GatewaySettings, build_gateway
from attention_sink.pilot import (
    CitationMode,
    PilotRunConfiguration,
    ProtocolBundle,
    RunKind,
    model_specs,
)
from attention_sink.pilot.cli import BUDGET_ROUNDING, proposed_budget
from tests.conftest import LOCAL_COUNTER_SOURCE

NOW = datetime(2026, 8, 29, tzinfo=UTC)


def configuration(bundle: ProtocolBundle, **overrides: object) -> PilotRunConfiguration:
    gateway = build_gateway(GatewaySettings.from_env(env={}))
    writer, embedding = model_specs(gateway)
    return PilotRunConfiguration.from_bundle(
        bundle,
        run_id="run_cfg",
        created_at=NOW,
        writer_model=writer,
        embedding_model=embedding,
        prompt_set_digest=gateway.prompts.prompt_set_digest(),
        app_version="0.1.0",
        **overrides,  # type: ignore[arg-type]
    )


def test_the_pilot_configures_exactly_the_six_canonical_arms(pilot_bundle: ProtocolBundle):
    assert configuration(pilot_bundle).arms == CANONICAL_ARMS
    assert len(CANONICAL_ARMS) == 6


def test_reference_arms_are_configured_off_rather_than_removed(pilot_bundle: ProtocolBundle):
    """The full and stateless arms still exist; this protocol simply does not run them."""
    arms = configuration(pilot_bundle).arms
    assert ArmId.ARM_FULL not in arms
    assert ArmId.ARM_STATELESS not in arms


def test_the_budget_and_its_counter_travel_together(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    budget = config.budget
    assert budget.max_active_tokens == config.memory_budget_tokens
    assert budget.counter_version == config.counter_version == "heuristic-v1"


def test_the_pin_resolves_to_one_arm_scoped_identifier(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    assert config.pinned_origin_seed_memory_id == "seed_01"
    assert config.pinned_origin_memory_id == make_memory_id(ArmId.ARM_SINK, 0)
    policies = config.policy_configuration
    assert policies.pinned_origin.pinned_memory_id == config.pinned_origin_memory_id


def test_the_dreamer_parameters_reach_the_summarising_arm(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    summarization = config.policy_configuration.summarization
    assert summarization.summary_target_token_limit == config.dreamer_target_summary_tokens
    assert summarization.safety_margin_tokens == config.dreamer_safety_margin_tokens
    assert summarization.min_sources == config.dreamer_min_sources
    assert summarization.fifo_fallback_enabled is (config.dreamer_fallback_rule == "fifo")


def test_a_fixture_run_may_not_call_itself_canonical(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle, run_kind=RunKind.AWS_CANONICAL)
    assert config.simulated
    assert config.canonical
    with pytest.raises(ValueError, match="but its models are simulated"):
        config.require_run_kind_consistent()


def test_a_local_fixture_run_that_admits_what_it_is_is_fine(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    assert config.run_kind is RunKind.LOCAL_FIXTURE
    assert not config.canonical
    assert config.token_count_source == LOCAL_COUNTER_SOURCE
    config.require_run_kind_consistent()


def test_a_local_run_may_not_be_driven_by_real_models(pilot_bundle: ProtocolBundle):
    """The credential boundary, stated as a refusal rather than as a promise."""
    config = configuration(pilot_bundle)
    real = config.writer_model.model_copy(update={"simulated": False})
    with pytest.raises(ValueError, match="but its models are real"):
        config.model_copy(
            update={"writer_model": real, "embedding_model": real}
        ).require_run_kind_consistent()


def test_a_staging_run_is_neither_canonical_nor_expected_to_be_simulated():
    assert not RunKind.AWS_STAGING.is_canonical
    assert not RunKind.AWS_STAGING.simulated_expected


def test_every_protocol_digest_is_recorded(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    assert set(config.protocol_content_hashes) == set(pilot_bundle.paths)
    assert all(v.startswith("sha256:") for v in config.protocol_content_hashes.values())


def test_the_checkpoints_are_zero_twelve_and_twenty_four(pilot_bundle: ProtocolBundle):
    config = configuration(pilot_bundle)
    assert config.checkpoint_cycles == (0, 12, 24)
    assert [c for c in range(25) if config.is_checkpoint(c)] == [0, 12, 24]


def test_the_citation_mode_is_recorded_on_the_run(pilot_bundle: ProtocolBundle):
    assert configuration(pilot_bundle).citation_mode is CitationMode.CLAIMED_VALIDATED


def test_an_uncalibrated_bundle_cannot_configure_a_run(pilot_bundle: ProtocolBundle):
    uncalibrated = pilot_bundle.model_copy(
        update={
            "protocol": pilot_bundle.protocol.model_copy(
                update={"memory_budget_tokens": None, "counter_version": None}
            )
        }
    )
    with pytest.raises(ValueError, match="no calibrated budget"):
        configuration(uncalibrated)


@pytest.mark.parametrize("seed_tokens", [1, 7, 100, 157, 1000])
def test_the_proposed_budget_always_leaves_room_and_rounds_up(seed_tokens: int):
    budget = proposed_budget(seed_tokens)
    assert budget > seed_tokens
    assert budget % BUDGET_ROUNDING == 0


def test_a_canonical_run_refuses_an_approximate_token_count(pilot_bundle: ProtocolBundle):
    """ADR-012: a canonical run is counted with the model's own tokeniser or not at all.

    The refusal is a validator rather than a convention, and it fires before a cycle
    can spend anything.
    """
    from attention_sink.pilot import ModelSpec
    from attention_sink.pilot.configuration import EXACT_TOKEN_COUNT_SOURCES

    real = ModelSpec(
        model_id="amazon.nova-lite-v1:0",
        region="us-east-1",
        temperature=0.7,
        top_p=0.9,
        max_output_tokens=1024,
        simulated=False,
    )
    approximate = configuration(pilot_bundle).model_copy(
        update={
            "run_kind": RunKind.AWS_CANONICAL,
            "writer_model": real,
            "embedding_model": real,
            "token_count_source": "local_fixture_heuristic",
        }
    )
    with pytest.raises(ValueError, match="approximation"):
        approximate.require_run_kind_consistent()

    exact = approximate.model_copy(
        update={"token_count_source": next(iter(EXACT_TOKEN_COUNT_SOURCES))}
    )
    exact.require_run_kind_consistent()
