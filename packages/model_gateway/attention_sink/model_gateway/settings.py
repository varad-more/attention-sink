"""Runtime and gateway configuration, resolved once at process start.

Two independent switches live here and they are deliberately not the same switch.

:class:`RuntimeMode` is a property of the *process*: whether what it publishes may
ever be presented as a real result. :class:`ModelMode` is a property of the *model
gateway*: whether calls reach Bedrock or a deterministic fake. They are separate
because running real Bedrock calls from a local process is legitimate -- that is what
the opt-in contract tests do -- while the reverse, a production process serving
fabricated generations, is the failure this whole module exists to prevent. The one
forbidden combination is checked in :meth:`GatewaySettings.from_env`.

Both resolvers fail closed. A gateway asked for Bedrock without a Region and a full
set of model identifiers refuses to start, because a fixture response presented as a
real generation would invalidate every result computed from it.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from os import environ
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = [
    "ConfigurationError",
    "GatewaySettings",
    "ModelConfig",
    "ModelMode",
    "RuntimeMode",
    "RuntimeSettings",
    "TokenCountSource",
    "WriterInference",
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
    """Whether this process's output may ever be presented as a real result."""

    LOCAL = "local"
    """Development. Output is labelled simulated and can never be canonical."""

    PRODUCTION = "production"
    """Serving canonical results. Fixture model responses are forbidden."""


class ModelMode(StrEnum):
    """Where the model gateway obtains responses."""

    BEDROCK = "bedrock"
    """Real Amazon Bedrock invocations against a configured Region and model set."""

    FIXTURE = "fixture"
    """Deterministic local responses. No AWS credentials required."""


class TokenCountSource(StrEnum):
    """Which counter denominates the active-memory budget.

    Not a fallback switch. ADR-011 makes the model's own tokeniser the production
    unit and forbids degrading to an approximation when it fails; ADR-012 adds that a
    deployment whose Region offers no model supporting Bedrock ``CountTokens`` may
    *declare* the approximate counter instead. The distinction is the guarantee: one
    is recorded in the run manifest before a single cycle runs, the other would be a
    unit change nobody could see afterwards.
    """

    BEDROCK = "bedrock"
    """Bedrock ``CountTokens``, against the writer's own model. No fallback."""

    HEURISTIC = "heuristic"
    """The versioned approximate counter. Never valid for a canonical run."""


class ModelConfig(BaseModel):
    """The five model roles and the Region they are invoked in.

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


class WriterInference(BaseModel):
    """Decoding settings, applied identically to every arm.

    Unlike a model identifier these carry compiled-in defaults, and the distinction
    is deliberate. ADR-006 forbids a default model because a vendor can change what
    an unspecified model resolves to, silently making two runs incomparable. A number
    this repository chooses cannot drift underneath a run: it is recorded on every
    call's metadata and in the settings record that reaches the manifest, so an
    unset value still has one visible, reproducible meaning.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, gt=0.0, le=1.0)
    writer_max_tokens: int = Field(default=1024, gt=0, le=32768)
    summary_max_tokens: int = Field(default=256, gt=0, le=32768)


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
        mode = _enum_from_env(source, "AS_RUNTIME_MODE", RuntimeMode, RuntimeMode.LOCAL)
        if mode is RuntimeMode.LOCAL:
            return cls(mode=mode, models=None)
        return cls(mode=mode, models=_resolve_models(source))


class GatewaySettings(BaseModel):
    """Everything the model gateway needs to make a call, resolved and validated.

    Immutable, serialisable, and complete: an adapter built from one of these never
    reads the environment again, so what a run recorded is what a run used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    mode: ModelMode
    models: ModelConfig | None = None
    inference: WriterInference = WriterInference()
    token_count_source: TokenCountSource = TokenCountSource.BEDROCK
    """What the budget is counted with. Recorded, never inferred."""

    request_timeout_seconds: int = Field(default=60, gt=0, le=900)
    max_model_retries: int = Field(default=3, ge=0, le=10)
    """Retries *after* the first attempt. Zero means one attempt and no retry."""

    @model_validator(mode="after")
    def _bedrock_requires_models(self) -> Self:
        if self.mode is ModelMode.BEDROCK and self.models is None:
            msg = "bedrock mode requires a Region and all five model identifiers"
            raise ValueError(msg)
        return self

    @property
    def is_simulated(self) -> bool:
        """Whether this gateway fabricates responses instead of invoking a model."""
        return self.mode is ModelMode.FIXTURE

    @property
    def region(self) -> str:
        """The Region calls are made in.

        Raises:
            ConfigurationError: This gateway is in fixture mode and has no Region.
        """
        if self.models is None:
            msg = "fixture mode has no Region; nothing is invoked"
            raise ConfigurationError(msg)
        return self.models.region

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> GatewaySettings:
        """Resolve gateway settings from the environment, failing closed.

        Args:
            env: Environment to read. Defaults to the process environment;
                injectable so tests never mutate global state.

        Raises:
            ConfigurationError: ``MODEL_MODE``, ``AS_RUNTIME_MODE``, or
                ``TOKEN_COUNT_SOURCE`` is unknown, a numeric setting is unparseable
                or out of range, Bedrock mode is missing a Region or a model
                identifier, or a production runtime asked for fixture responses.
        """
        source = environ if env is None else env
        mode = _enum_from_env(source, "MODEL_MODE", ModelMode, ModelMode.FIXTURE)
        runtime = _enum_from_env(source, "AS_RUNTIME_MODE", RuntimeMode, RuntimeMode.LOCAL)
        if mode is ModelMode.FIXTURE and runtime is RuntimeMode.PRODUCTION:
            msg = (
                "AS_RUNTIME_MODE=production cannot run with MODEL_MODE=fixture. A "
                "production process must not serve fabricated generations as real ones."
            )
            raise ConfigurationError(msg)

        settings = {
            "mode": mode,
            # Passed as data rather than as a constructed model, so that a value out
            # of range and a value of the wrong type fail the same way: as a
            # configuration error naming the variable, not as a validation error
            # escaping a resolver whose whole purpose is to fail closed.
            "inference": {
                "temperature": _number(source, "WRITER_TEMPERATURE", 0.7, float),
                "top_p": _number(source, "WRITER_TOP_P", 0.9, float),
                "writer_max_tokens": _number(source, "WRITER_MAX_TOKENS", 1024, int),
                "summary_max_tokens": _number(source, "SUMMARY_MAX_TOKENS", 256, int),
            },
            "request_timeout_seconds": _number(source, "REQUEST_TIMEOUT_SECONDS", 60, int),
            "max_model_retries": _number(source, "MAX_MODEL_RETRIES", 3, int),
            "models": _resolve_models(source) if mode is ModelMode.BEDROCK else None,
            "token_count_source": _enum_from_env(
                source, "TOKEN_COUNT_SOURCE", TokenCountSource, TokenCountSource.BEDROCK
            ),
        }
        try:
            return cls.model_validate(settings)
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc


def _enum_from_env[E: StrEnum](
    source: Mapping[str, str], variable: str, enum: type[E], default: E
) -> E:
    """Read a closed-vocabulary setting, rejecting anything outside the vocabulary."""
    raw = source.get(variable, default.value).strip().lower()
    try:
        return enum(raw)
    except ValueError as exc:
        known = ", ".join(member.value for member in enum)
        msg = f"{variable} must be one of: {known} (got {raw!r})"
        raise ConfigurationError(msg) from exc


def _number[N: (int, float)](
    source: Mapping[str, str], variable: str, default: N, parse: type[N]
) -> N:
    """Read a numeric setting, refusing to guess at anything unparseable."""
    raw = source.get(variable, "").strip()
    if not raw:
        return default
    try:
        return parse(raw)
    except ValueError as exc:
        msg = f"{variable} must be {parse.__name__} (got {raw!r})"
        raise ConfigurationError(msg) from exc


def _resolve_models(source: Mapping[str, str]) -> ModelConfig:
    """Resolve the Region and five model identifiers, or refuse to continue."""
    required = (*_MODEL_ENV_VARS, ("region", "AWS_REGION"))
    values = {field: source.get(variable, "").strip() for field, variable in required}
    missing = sorted(variable for field, variable in required if not values[field])
    if missing:
        msg = (
            "model access is missing required configuration: "
            f"{', '.join(missing)}. Refusing to start rather than serve "
            "simulated responses as real ones."
        )
        raise ConfigurationError(msg)
    return ModelConfig.model_validate(values)
