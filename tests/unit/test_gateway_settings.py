"""Gateway configuration resolves completely, or refuses to resolve at all."""

from __future__ import annotations

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
