"""The fixture gateway may exist only in local mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from attention_sink.model_gateway import (
    ConfigurationError,
    FixtureModelGateway,
    FixtureNotFoundError,
    RuntimeMode,
    RuntimeSettings,
)

LOCAL = RuntimeSettings(mode=RuntimeMode.LOCAL)


@pytest.fixture
def fixture_root(tmp_path: Path) -> Path:
    payload = {
        "schema_version": 1,
        "task": "writer",
        "simulated": True,
        "responses": {"smoke": {"thought": "a recorded thought"}},
    }
    (tmp_path / "writer.json").write_text(json.dumps(payload), encoding="utf-8")
    return tmp_path


def test_refuses_to_exist_in_production(fixture_root: Path):
    production = RuntimeSettings.from_env(
        env={
            "AS_RUNTIME_MODE": "production",
            "AWS_REGION": "us-east-1",
            "WRITER_MODEL_ID": "w",
            "AUDITOR_MODEL_ID": "a",
            "JUDGE_MODEL_ID": "j",
            "SUMMARY_MODEL_ID": "s",
            "EMBEDDING_MODEL_ID": "e",
        }
    )

    with pytest.raises(ConfigurationError, match="local"):
        FixtureModelGateway(production, fixture_root)


def test_missing_fixture_directory_is_a_configuration_error(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="does not exist"):
        FixtureModelGateway(LOCAL, tmp_path / "absent")


def test_returns_the_recorded_response(fixture_root: Path):
    gateway = FixtureModelGateway(LOCAL, fixture_root)

    assert gateway.respond("writer", "smoke") == {"thought": "a recorded thought"}
    assert gateway.simulated is True


def test_repeated_calls_are_identical(fixture_root: Path):
    gateway = FixtureModelGateway(LOCAL, fixture_root)

    assert gateway.respond("writer", "smoke") == gateway.respond("writer", "smoke")


def test_unknown_task_names_the_path_it_looked_for(fixture_root: Path):
    gateway = FixtureModelGateway(LOCAL, fixture_root)

    with pytest.raises(FixtureNotFoundError, match="judge"):
        gateway.respond("judge", "smoke")


def test_unknown_key_lists_what_is_available(fixture_root: Path):
    gateway = FixtureModelGateway(LOCAL, fixture_root)

    with pytest.raises(FixtureNotFoundError, match="smoke"):
        gateway.respond("writer", "absent")


def test_fixture_file_must_declare_itself_simulated(tmp_path: Path):
    payload = {"schema_version": 1, "task": "writer", "simulated": False, "responses": {"k": {}}}
    (tmp_path / "writer.json").write_text(json.dumps(payload), encoding="utf-8")
    gateway = FixtureModelGateway(LOCAL, tmp_path)

    with pytest.raises(ValueError, match="simulated"):
        gateway.load("writer")
