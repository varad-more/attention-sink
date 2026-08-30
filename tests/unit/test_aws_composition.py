"""The composition root: what a deployed process decides it is talking to.

Small surface, high stakes. Every test here is about a combination that must be
refused before a single request is made, because each one would produce output that
looks real and is not, or spend against an account nobody meant to use.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from attention_sink.aws import composition
from attention_sink.aws.composition import Runtime, build_runtime, protocol_root, reset_runtime
from attention_sink.model_gateway import ConfigurationError

BASE = {
    "AS_TABLE_NAME": "pilot-table",
    "AS_PILOT_RUN_ID": "run_staging",
    "AWS_REGION": "us-east-1",
    "WRITER_MODEL_ID": "amazon.nova-lite-v1:0",
    "AUDITOR_MODEL_ID": "amazon.nova-lite-v1:0",
    "JUDGE_MODEL_ID": "amazon.nova-lite-v1:0",
    "SUMMARY_MODEL_ID": "amazon.nova-lite-v1:0",
    "EMBEDDING_MODEL_ID": "amazon.titan-embed-text-v2:0",
}


@pytest.fixture(autouse=True)
def _clean_runtime() -> None:
    """Forget any cached runtime, so one test cannot configure the next."""
    reset_runtime()


def _environment(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for name in (
        "AS_DEPLOYMENT_ENVIRONMENT",
        "MODEL_MODE",
        "AS_RUNTIME_MODE",
        "AS_EXECUTION_ENABLED",
        "ALLOW_BEDROCK_CALLS",
        "AS_MAX_CYCLES",
        "TOKEN_COUNT_SOURCE",
        *BASE,
    ):
        monkeypatch.delenv(name, raising=False)
    for name, value in values.items():
        monkeypatch.setenv(name, value)


def test_a_deployed_environment_refuses_a_fixture_gateway(monkeypatch: pytest.MonkeyPatch):
    """The failure this whole module exists to prevent, at the deployment level.

    ``GatewaySettings`` already refuses fixtures for a production *runtime*. This is
    the other half: a staging or production *deployment* must not record fabricated
    generations against a real table, whatever the runtime mode says.
    """
    _environment(
        monkeypatch,
        AS_DEPLOYMENT_ENVIRONMENT="staging",
        MODEL_MODE="fixture",
        AS_TABLE_NAME="pilot-table",
        AS_PILOT_RUN_ID="run_staging",
    )
    with pytest.raises(ConfigurationError, match="cannot run with MODEL_MODE=fixture"):
        build_runtime("test")


def test_a_local_environment_may_use_fixtures(monkeypatch: pytest.MonkeyPatch):
    _environment(
        monkeypatch,
        AS_DEPLOYMENT_ENVIRONMENT="local",
        MODEL_MODE="fixture",
        AS_TABLE_NAME="pilot-table",
        AS_PILOT_RUN_ID="run_local",
        AWS_DEFAULT_REGION="us-east-1",
    )
    runtime = build_runtime("test-local")
    assert runtime.gateway.simulated
    assert runtime.repository.table_name == "pilot-table"
    assert runtime.settings.run_id == "run_local"


def test_the_runtime_is_built_once_per_service(monkeypatch: pytest.MonkeyPatch):
    """A Lambda that rebuilt its clients per invocation would pay a handshake a cycle."""
    _environment(
        monkeypatch,
        AS_TABLE_NAME="pilot-table",
        AS_PILOT_RUN_ID="run_local",
        AWS_DEFAULT_REGION="us-east-1",
    )
    assert build_runtime("cached") is build_runtime("cached")
    assert build_runtime("cached") is not build_runtime("other")


def test_a_missing_table_name_refuses_before_any_request(monkeypatch: pytest.MonkeyPatch):
    _environment(monkeypatch, AS_PILOT_RUN_ID="run_local")
    with pytest.raises(ConfigurationError):
        build_runtime("test")


def test_the_protocol_root_follows_the_lambda_task_root(monkeypatch: pytest.MonkeyPatch):
    """Inside a Lambda the package is unpacked somewhere the repository layout is not."""
    monkeypatch.delenv(composition.PROTOCOL_ROOT_ENV, raising=False)
    monkeypatch.setenv("LAMBDA_TASK_ROOT", "/var/task")
    assert protocol_root() == Path("/var/task/experiment/pilot")

    monkeypatch.delenv("LAMBDA_TASK_ROOT", raising=False)
    assert protocol_root() == Path("experiment/pilot")

    monkeypatch.setenv(composition.PROTOCOL_ROOT_ENV, "/somewhere/else")
    assert protocol_root() == Path("/somewhere/else")


def test_a_runtime_hands_out_a_service_and_an_analysis_over_its_own_store(
    monkeypatch: pytest.MonkeyPatch,
):
    _environment(
        monkeypatch,
        AS_TABLE_NAME="pilot-table",
        AS_PILOT_RUN_ID="run_local",
        AWS_DEFAULT_REGION="us-east-1",
    )
    runtime: Runtime = build_runtime("services")
    assert runtime.service().repository is runtime.repository
    assert runtime.analysis().repository is runtime.repository
    # Built per call, never held: a service that outlived one invocation would be
    # one more thing carrying state between two runs of a handler.
    assert runtime.service() is not runtime.service()
