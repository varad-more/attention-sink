"""Gateway configuration resolves completely, or refuses to resolve at all."""

from __future__ import annotations

from typing import Any

import pytest

from attention_sink.model_gateway import (
    ConfigurationError,
    GatewaySettings,
    ModelMode,
    RuntimeMode,
)

BEDROCK_ENV = {
    "MODEL_MODE": "bedrock",
    "AWS_REGION": "eu-west-2",
    "WRITER_MODEL_ID": "writer-model",
    "AUDITOR_MODEL_ID": "auditor-model",
    "JUDGE_MODEL_ID": "judge-model",
    "SUMMARY_MODEL_ID": "summary-model",
    "EMBEDDING_MODEL_ID": "embedding-model",
}


def test_an_unconfigured_process_serves_fixtures_and_says_so():
    settings = GatewaySettings.from_env(env={})

    assert settings.mode is ModelMode.FIXTURE
    assert settings.is_simulated is True
    assert settings.models is None


def test_fixture_mode_has_no_region_to_offer():
    settings = GatewaySettings.from_env(env={})

    with pytest.raises(ConfigurationError, match="no Region"):
        _ = settings.region


def test_bedrock_mode_resolves_every_role_and_the_region():
    settings = GatewaySettings.from_env(env=BEDROCK_ENV)

    assert settings.is_simulated is False
    assert settings.region == "eu-west-2"
    assert settings.models is not None
    assert settings.models.judge_model_id == "judge-model"


@pytest.mark.parametrize("missing", sorted(set(BEDROCK_ENV) - {"MODEL_MODE"}))
def test_bedrock_mode_refuses_to_start_when_any_identifier_is_missing(missing: str):
    env = {key: value for key, value in BEDROCK_ENV.items() if key != missing}

    with pytest.raises(ConfigurationError) as excinfo:
        GatewaySettings.from_env(env=env)

    assert missing in str(excinfo.value)


def test_a_production_runtime_may_not_serve_fixture_responses():
    with pytest.raises(ConfigurationError, match="production"):
        GatewaySettings.from_env(env={"AS_RUNTIME_MODE": "production", "MODEL_MODE": "fixture"})


def test_a_local_runtime_may_call_bedrock():
    """The opt-in contract suite does exactly this, and it is not the dangerous case."""
    settings = GatewaySettings.from_env(env={**BEDROCK_ENV, "AS_RUNTIME_MODE": "local"})

    assert settings.mode is ModelMode.BEDROCK
    assert RuntimeMode.LOCAL.value == "local"


def test_unknown_model_mode_is_rejected_rather_than_coerced():
    with pytest.raises(ConfigurationError, match="MODEL_MODE"):
        GatewaySettings.from_env(env={"MODEL_MODE": "openai"})


def test_inference_parameters_come_from_the_environment():
    settings = GatewaySettings.from_env(
        env={
            "WRITER_TEMPERATURE": "0.2",
            "WRITER_TOP_P": "0.5",
            "WRITER_MAX_TOKENS": "700",
            "SUMMARY_MAX_TOKENS": "120",
            "REQUEST_TIMEOUT_SECONDS": "45",
            "MAX_MODEL_RETRIES": "1",
        }
    )

    assert settings.inference.temperature == 0.2
    assert settings.inference.top_p == 0.5
    assert settings.inference.writer_max_tokens == 700
    assert settings.inference.summary_max_tokens == 120
    assert settings.request_timeout_seconds == 45
    assert settings.max_model_retries == 1


def test_an_unparseable_number_stops_the_process_rather_than_defaulting():
    with pytest.raises(ConfigurationError, match="WRITER_TEMPERATURE"):
        GatewaySettings.from_env(env={"WRITER_TEMPERATURE": "warm"})


def test_a_number_outside_its_range_is_refused():
    with pytest.raises(ConfigurationError, match="temperature"):
        GatewaySettings.from_env(env={"WRITER_TEMPERATURE": "9"})


def test_bedrock_settings_cannot_be_constructed_without_models():
    with pytest.raises(ValueError, match="five model identifiers"):
        GatewaySettings(mode=ModelMode.BEDROCK)


def test_settings_round_trip_through_json():
    settings = GatewaySettings.from_env(env=BEDROCK_ENV)

    assert GatewaySettings.model_validate_json(settings.model_dump_json()) == settings


# ------------------------------------------------------------ the token counter


def test_the_budget_is_counted_by_the_model_unless_a_deployment_says_otherwise():
    """ADR-012: declared, never inferred, and never a fallback."""
    from attention_sink.model_gateway import TokenCountSource

    assert GatewaySettings.from_env(env={}).token_count_source is TokenCountSource.BEDROCK
    declared = GatewaySettings.from_env(env={"TOKEN_COUNT_SOURCE": "heuristic"})
    assert declared.token_count_source is TokenCountSource.HEURISTIC


def test_an_unknown_counter_source_is_refused_by_name():
    with pytest.raises(ConfigurationError, match="TOKEN_COUNT_SOURCE"):
        GatewaySettings.from_env(env={"TOKEN_COUNT_SOURCE": "approximate"})


def test_a_bedrock_gateway_builds_the_counter_its_settings_declare():
    from attention_sink.model_gateway import (
        ApproximateTokenCounter,
        BedrockTokenCounter,
        build_gateway,
    )

    models = {
        "AWS_REGION": "us-east-1",
        "WRITER_MODEL_ID": "amazon.nova-lite-v1:0",
        "AUDITOR_MODEL_ID": "amazon.nova-lite-v1:0",
        "JUDGE_MODEL_ID": "amazon.nova-lite-v1:0",
        "SUMMARY_MODEL_ID": "amazon.nova-lite-v1:0",
        "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
        "MODEL_MODE": "bedrock",
    }

    class _NoClient:
        """A client that would fail if the factory called it. It never does."""

        def count_tokens(self, **_: Any) -> Any:
            raise AssertionError("no counting happens while building a gateway")

        def invoke_model(self, **_: Any) -> Any:
            raise AssertionError("no invocation happens while building a gateway")

    unused: Any = _NoClient()
    exact = build_gateway(GatewaySettings.from_env(env=models), client=unused)
    assert isinstance(exact.token_counter, BedrockTokenCounter)

    approximate = build_gateway(
        GatewaySettings.from_env(env={**models, "TOKEN_COUNT_SOURCE": "heuristic"}),
        client=unused,
    )
    assert isinstance(approximate.token_counter, ApproximateTokenCounter)
    # Approximate, but not simulated: the models behind this run are real.
    assert approximate.token_counter.simulated is False
    assert approximate.token_counter.version == "heuristic-v1"
    assert approximate.token_counter.model_id == "amazon.nova-lite-v1:0"
