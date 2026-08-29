"""Runtime configuration for model access, resolved once at process start.

Two modes exist and they behave in opposite ways when configuration is missing.
Local mode is permissive and serves deterministic fixtures so that a contributor
with no AWS account can run the system. Production mode fails closed: an
unconfigured production process must refuse to start rather than silently fall back
to fixtures, because a fixture response presented as a real generation would
invalidate every result computed from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from os import environ
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ConfigurationError",
    "ModelConfig",
    "RuntimeMode",
    "RuntimeSettings",
]

_MODEL_ENV_VARS: tuple[tuple[str, str], ...] = (
    ("writer_model_id", "WRITER_MODEL_ID"),
    ("auditor_model_id", "AUDITOR_MODEL_ID"),
    ("judge_model_id", "JUDGE_MODEL_ID"),
    ("summary_model_id", "SUMMARY_MODEL_ID"),
    ("embedding_model_id", "EMBEDDING_MODEL_ID"),
)
"""Settings field to environment variable, for the five roles the experiment uses.

No default is compiled in for any of them. A model identifier baked into code would
be an unrecorded experimental parameter; every run must declare the models it used
and store them in its manifest.
"""


class ConfigurationError(RuntimeError):
    """Configuration is absent or inconsistent for the requested runtime mode."""


class RuntimeMode(StrEnum):
    """How this process obtains model responses."""

    LOCAL = "local"
    """Deterministic fixtures. No AWS credentials required. Never canonical."""

    PRODUCTION = "production"
    """Real Bedrock invocations against a configured Region and model set."""


class ModelConfig(BaseModel):
    """The five model roles, as resolved for this process.

    Stored verbatim in every run manifest. Two runs that used different values here
    are different experiments even if nothing else changed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    region: str = Field(min_length=1)
    writer_model_id: str = Field(min_length=1)
    auditor_model_id: str = Field(min_length=1)
    judge_model_id: str = Field(min_length=1)
    summary_model_id: str = Field(min_length=1)
    embedding_model_id: str = Field(min_length=1)


class RuntimeSettings(BaseModel):
    """Resolved runtime configuration, including whether output is simulated."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    mode: RuntimeMode
    models: ModelConfig | None = None

    @model_validator(mode="after")
    def _production_requires_models(self) -> Self:
        if self.mode is RuntimeMode.PRODUCTION and self.models is None:
            msg = "production mode requires a fully resolved model configuration"
            raise ValueError(msg)
        return self

    @property
    def is_simulated(self) -> bool:
        """Whether responses come from fixtures rather than a real model.

        Surfaced through the API and rendered as a banner by the web client. A user
        must never have to guess whether what they are reading actually happened.
        """
        return self.mode is RuntimeMode.LOCAL

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RuntimeSettings:
        """Resolve settings from the environment, failing closed in production.

        Args:
            env: Environment to read. Defaults to the process environment;
                injectable so tests never mutate global state.

        Raises:
            ConfigurationError: ``AS_RUNTIME_MODE`` is not a known mode, or
                production mode was selected without a Region and all five model
                identifiers.
        """
        source = environ if env is None else env
        raw_mode = source.get("AS_RUNTIME_MODE", RuntimeMode.LOCAL.value).strip().lower()
        try:
            mode = RuntimeMode(raw_mode)
        except ValueError as exc:
            known = ", ".join(m.value for m in RuntimeMode)
            msg = f"AS_RUNTIME_MODE must be one of: {known} (got {raw_mode!r})"
            raise ConfigurationError(msg) from exc

        if mode is RuntimeMode.LOCAL:
            return cls(mode=mode, models=None)

        required = (*_MODEL_ENV_VARS, ("region", "AWS_REGION"))
        values = {field: source.get(var, "").strip() for field, var in required}
        missing = sorted(var for field, var in required if not values[field])
        if missing:
            msg = (
                "production mode is missing required configuration: "
                f"{', '.join(missing)}. Refusing to start rather than serve "
                "simulated responses as real ones."
            )
            raise ConfigurationError(msg)
        return cls(
            mode=mode,
            models=ModelConfig(
                region=values["region"],
                writer_model_id=values["writer_model_id"],
                auditor_model_id=values["auditor_model_id"],
                judge_model_id=values["judge_model_id"],
                summary_model_id=values["summary_model_id"],
                embedding_model_id=values["embedding_model_id"],
            ),
        )
