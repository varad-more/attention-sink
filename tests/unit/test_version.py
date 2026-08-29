"""Version identity resolves from injected environment, never from global state."""

from __future__ import annotations

from attention_sink.protocol import PROTOCOL_VERSION, SCHEMA_VERSION, current_version


def test_defaults_when_environment_is_empty():
    info = current_version(env={})

    assert info.schema_version == SCHEMA_VERSION
    assert info.protocol_version == PROTOCOL_VERSION
    assert info.git_commit is None
    assert info.app_version


def test_protocol_version_is_overridable_because_experiments_own_it():
    info = current_version(env={"AS_PROTOCOL_VERSION": "2027.01-final"})

    assert info.protocol_version == "2027.01-final"


def test_blank_environment_values_are_treated_as_absent():
    info = current_version(env={"AS_PROTOCOL_VERSION": "   ", "AS_GIT_COMMIT": ""})

    assert info.protocol_version == PROTOCOL_VERSION
    assert info.git_commit is None


def test_git_commit_is_carried_through_for_provenance():
    info = current_version(env={"AS_GIT_COMMIT": "0a1b2c3"})

    assert info.git_commit == "0a1b2c3"
    assert info.as_manifest_fields()["git_commit"] == "0a1b2c3"


def test_manifest_fields_carry_the_full_identity():
    fields = current_version(env={}).as_manifest_fields()

    assert set(fields) == {"schema_version", "protocol_version", "app_version", "git_commit"}
