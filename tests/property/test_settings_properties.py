"""Invariants of runtime configuration, over generated environments."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from attention_sink.model_gateway import ConfigurationError, RuntimeMode, RuntimeSettings

REQUIRED_PRODUCTION_VARS = (
    "AWS_REGION",
    "WRITER_MODEL_ID",
    "AUDITOR_MODEL_ID",
    "JUDGE_MODEL_ID",
    "SUMMARY_MODEL_ID",
    "EMBEDDING_MODEL_ID",
)

identifiers = st.text(min_size=1, max_size=40).filter(lambda s: s.strip())
blanks = st.sampled_from(["", " ", "\t", "\n  "])


@given(
    present=st.lists(st.sampled_from(REQUIRED_PRODUCTION_VARS), unique=True),
    value=identifiers,
)
def test_production_starts_only_when_every_required_value_is_present(
    present: list[str], value: str
) -> None:
    """Partial production configuration must never resolve to a usable process."""
    env = {"AS_RUNTIME_MODE": "production"} | dict.fromkeys(present, value)

    if len(present) == len(REQUIRED_PRODUCTION_VARS):
        assert RuntimeSettings.from_env(env=env).is_simulated is False
    else:
        try:
            RuntimeSettings.from_env(env=env)
        except ConfigurationError:
            return
        raise AssertionError("production mode started with missing configuration")


@given(blank=blanks)
def test_blank_values_do_not_count_as_configuration(blank: str) -> None:
    """A variable set to whitespace is absent, not satisfied."""
    env = {"AS_RUNTIME_MODE": "production"} | dict.fromkeys(REQUIRED_PRODUCTION_VARS, "x")
    env["WRITER_MODEL_ID"] = blank

    try:
        RuntimeSettings.from_env(env=env)
    except ConfigurationError as exc:
        assert "WRITER_MODEL_ID" in str(exc)
        return
    raise AssertionError("a blank model identifier was accepted")


@given(mode=st.text(max_size=20))
def test_mode_resolution_never_silently_produces_production(mode: str) -> None:
    """Only an explicit, recognised value selects production mode."""
    try:
        settings = RuntimeSettings.from_env(env={"AS_RUNTIME_MODE": mode})
    except ConfigurationError:
        return
    assert settings.mode is RuntimeMode.LOCAL
