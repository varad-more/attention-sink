"""Local mode is permissive; production mode fails closed."""

from __future__ import annotations

import pytest

from attention_sink.model_gateway import ConfigurationError, RuntimeMode, RuntimeSettings

COMPLETE_PRODUCTION_ENV = {
    "AS_RUNTIME_MODE": "production",
    "AWS_REGION": "eu-west-1",
    "WRITER_MODEL_ID": "writer-model",
    "AUDITOR_MODEL_ID": "auditor-model",
    "JUDGE_MODEL_ID": "judge-model",
    "SUMMARY_MODEL_ID": "summary-model",
    "EMBEDDING_MODEL_ID": "embedding-model",
}


def test_defaults_to_local_so_an_unconfigured_process_is_never_canonical():
    settings = RuntimeSettings.from_env(env={})

    assert settings.mode is RuntimeMode.LOCAL
    assert settings.is_simulated is True
    assert settings.models is None


def test_local_mode_needs_no_aws_configuration_at_all():
    settings = RuntimeSettings.from_env(env={"AS_RUNTIME_MODE": "local"})

    assert settings.is_simulated is True


def test_production_mode_resolves_every_model_role():
    settings = RuntimeSettings.from_env(env=COMPLETE_PRODUCTION_ENV)

    assert settings.is_simulated is False
    assert settings.models is not None
    assert settings.models.region == "eu-west-1"
    assert settings.models.writer_model_id == "writer-model"
    assert settings.models.embedding_model_id == "embedding-model"


@pytest.mark.parametrize("missing", sorted(set(COMPLETE_PRODUCTION_ENV) - {"AS_RUNTIME_MODE"}))
def test_production_mode_refuses_to_start_when_any_value_is_missing(missing: str):
    env = {k: v for k, v in COMPLETE_PRODUCTION_ENV.items() if k != missing}

    with pytest.raises(ConfigurationError) as excinfo:
        RuntimeSettings.from_env(env=env)

    assert missing in str(excinfo.value)


def test_unknown_mode_is_rejected_rather_than_coerced():
    with pytest.raises(ConfigurationError, match="AS_RUNTIME_MODE"):
        RuntimeSettings.from_env(env={"AS_RUNTIME_MODE": "staging"})


def test_mode_is_read_case_insensitively_with_surrounding_whitespace():
    settings = RuntimeSettings.from_env(
        env={**COMPLETE_PRODUCTION_ENV, "AS_RUNTIME_MODE": " Production "}
    )

    assert settings.mode is RuntimeMode.PRODUCTION


def test_production_settings_cannot_be_constructed_without_models():
    with pytest.raises(ValueError, match="fully resolved model configuration"):
        RuntimeSettings(mode=RuntimeMode.PRODUCTION)
