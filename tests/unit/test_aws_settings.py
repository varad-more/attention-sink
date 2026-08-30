"""What a deployed process is allowed to do, and how it refuses.

Every test here is about a switch that defaults to off. The failure these guard
against is not a crash: it is a deployment that quietly runs, quietly spends, or
quietly records a fabrication against a real table.
"""

from __future__ import annotations

import pytest

from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.model_gateway import ConfigurationError
from attention_sink.pilot.configuration import RunKind

BASE = {"AS_TABLE_NAME": "pilot-table", "AS_PILOT_RUN_ID": "run_staging"}


def settings(**overrides: str) -> AwsSettings:
    return AwsSettings.from_env(env={**BASE, **overrides})


def test_everything_dangerous_is_off_unless_it_is_turned_on():
    resolved = settings()
    assert resolved.environment is DeploymentEnvironment.LOCAL
    assert not resolved.execution_enabled
    assert not resolved.allow_bedrock_calls
    assert not resolved.canonical


def test_a_missing_table_name_is_refused():
    with pytest.raises(ConfigurationError):
        AwsSettings.from_env(env={"AS_PILOT_RUN_ID": "run_x"})


def test_a_missing_run_id_is_refused():
    with pytest.raises(ConfigurationError):
        AwsSettings.from_env(env={"AS_TABLE_NAME": "pilot-table"})


def test_an_unknown_environment_is_refused_by_name():
    with pytest.raises(ConfigurationError, match="AS_DEPLOYMENT_ENVIRONMENT"):
        settings(AS_DEPLOYMENT_ENVIRONMENT="prod")


@pytest.mark.parametrize("raw", ["yes", "on", "TRUE ", "2", "", "no"])
def test_only_one_and_true_arm_a_switch(raw: str):
    """A typo must not arm a deployment, so the vocabulary is exactly two words."""
    assert settings(AS_EXECUTION_ENABLED=raw).execution_enabled == (raw.strip().lower() == "true")


@pytest.mark.parametrize("raw", ["1", "true", "True"])
def test_the_two_words_that_do_arm_it(raw: str):
    assert settings(AS_EXECUTION_ENABLED=raw).execution_enabled


def test_a_disabled_deployment_refuses_to_execute():
    with pytest.raises(ConfigurationError, match="execution is disabled"):
        settings().require_can_execute()


def test_a_staging_deployment_will_not_execute_without_bedrock_armed():
    """Two locks on one door. They fail differently and both have to be open."""
    armed = settings(AS_DEPLOYMENT_ENVIRONMENT="staging", AS_EXECUTION_ENABLED="1")
    with pytest.raises(ConfigurationError, match="ALLOW_BEDROCK_CALLS"):
        armed.require_can_execute()
    both = settings(
        AS_DEPLOYMENT_ENVIRONMENT="staging",
        AS_EXECUTION_ENABLED="1",
        ALLOW_BEDROCK_CALLS="1",
    )
    both.require_can_execute()


def test_a_local_deployment_executes_without_bedrock():
    """The local path is the fixture path, and it must not need a provider."""
    settings(AS_EXECUTION_ENABLED="1").require_can_execute()


def test_only_production_may_be_marked_canonical():
    with pytest.raises(ConfigurationError, match="cannot be marked canonical"):
        settings(AS_DEPLOYMENT_ENVIRONMENT="staging", AS_CANONICAL="1")
    assert settings(AS_DEPLOYMENT_ENVIRONMENT="production", AS_CANONICAL="1").canonical


def test_a_production_deployment_is_not_canonical_by_default():
    """Deploying to the production account must not create the registered run."""
    production = settings(AS_DEPLOYMENT_ENVIRONMENT="production")
    assert not production.canonical
    assert production.run_kind is RunKind.AWS_STAGING


def test_a_staging_run_is_staging_kind():
    assert settings(AS_DEPLOYMENT_ENVIRONMENT="staging").run_kind is RunKind.AWS_STAGING
    assert settings().run_kind is RunKind.LOCAL_FIXTURE


def test_a_cycle_ceiling_is_optional_and_must_be_a_number():
    assert settings().maximum_cycles is None
    assert settings(AS_MAX_CYCLES="3").maximum_cycles == 3
    with pytest.raises(ConfigurationError, match="AS_MAX_CYCLES"):
        settings(AS_MAX_CYCLES="three")


def test_allowed_origins_are_a_list_and_never_a_wildcard():
    resolved = settings(AS_ALLOWED_ORIGINS="https://a.example, https://b.example ,")
    assert resolved.allowed_origins == ("https://a.example", "https://b.example")
    assert settings().allowed_origins == ()
