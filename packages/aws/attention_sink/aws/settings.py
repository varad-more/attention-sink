"""What a deployed process is allowed to do, resolved once and failing closed.

Three switches decide whether anything happens at all, and all three default to
"no".

``AS_EXECUTION_ENABLED`` gates advancing a run. ``ALLOW_BEDROCK_CALLS`` gates
reaching a model provider. ``AS_DEPLOYMENT_ENVIRONMENT`` decides what the output may
ever be called. A deployment that is missing a variable therefore does nothing rather
than doing something expensive, which is the correct behaviour for a stack whose
failure mode is a bill and a fabricated result.

These are separate from ``GatewaySettings`` deliberately. That record answers "which
model, with what decoding" and is stamped into a run manifest as an experimental
parameter. This one answers "may this process act", which is an operational question
and must never be mistaken for part of the experiment.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from os import environ
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from attention_sink.model_gateway import ConfigurationError
from attention_sink.pilot.configuration import RunKind

__all__ = ["AwsSettings", "DeploymentEnvironment"]


class DeploymentEnvironment(StrEnum):
    """Which deployment this process belongs to."""

    LOCAL = "local"
    """A developer's machine or a test. Never talks to a deployed table."""

    STAGING = "staging"
    """Real AWS, real models, non-canonical output. What Phase 7 deploys."""

    PRODUCTION = "production"
    """Real AWS, and the only environment a canonical run may ever exist in."""

    @property
    def default_run_kind(self) -> RunKind:
        """What a run created in this environment is, unless it is a local fixture."""
        return {
            DeploymentEnvironment.LOCAL: RunKind.LOCAL_FIXTURE,
            DeploymentEnvironment.STAGING: RunKind.AWS_STAGING,
            DeploymentEnvironment.PRODUCTION: RunKind.AWS_STAGING,
        }[self]
        # Production defaults to staging-kind on purpose. A canonical run is created
        # by an explicit, separate operation that names AWS_CANONICAL; nothing gets
        # there by deploying to the production account.


class AwsSettings(BaseModel):
    """Everything a deployed handler needs, validated once at cold start."""

    model_config = ConfigDict(frozen=True, extra="forbid", protected_namespaces=())

    schema_version: Literal[1] = 1
    environment: DeploymentEnvironment
    table_name: str = Field(min_length=1, max_length=255)
    run_id: str = Field(min_length=1, max_length=128)

    export_bucket: str | None = None
    event_bus_name: str | None = None
    """Absent means the default bus. The read API needs neither and is given neither."""

    execution_enabled: bool = False
    """Whether the run-cycle handler may advance the run at all.

    False in every environment until an operator turns it on. A scheduler that fires
    against a disabled deployment records that it was disabled and stops, which is
    what makes "deployed but not running" a state rather than an accident.
    """

    allow_bedrock_calls: bool = False
    """Whether a real provider may be invoked. Independent of ``MODEL_MODE``.

    Two locks on one door, because they fail differently: ``MODEL_MODE`` wrong means
    fabricated output presented as real, and this one wrong means money spent by a
    deployment nobody meant to arm.
    """

    maximum_cycles: int | None = Field(default=None, gt=0)
    """A ceiling below the protocol's own, for an environment that must not run the
    whole experiment. Staging sets it short; production leaves it unset and the
    protocol's twenty-four stands."""

    allowed_origins: tuple[str, ...] = ()
    lock_ttl_seconds: int = Field(default=300, gt=0, le=3600)
    canonical: bool = False
    """Whether this deployment may create a canonical run. Never true by
    configuration alone -- see :meth:`require_can_execute`."""

    @model_validator(mode="after")
    def _refuse_canonical_outside_production(self) -> Self:
        if self.canonical and self.environment is not DeploymentEnvironment.PRODUCTION:
            msg = (
                f"a {self.environment.value} deployment cannot be marked canonical; "
                f"only a production deployment may hold the registered experiment"
            )
            raise ValueError(msg)
        return self

    @property
    def run_kind(self) -> RunKind:
        """What a run created by this deployment is."""
        return RunKind.AWS_CANONICAL if self.canonical else self.environment.default_run_kind

    def require_can_execute(self) -> None:
        """Refuse to advance a run this deployment is not armed for.

        Raises:
            ConfigurationError: Execution is disabled, or a non-local deployment
                would run without a provider.
        """
        if not self.execution_enabled:
            msg = (
                f"execution is disabled for the {self.environment.value} deployment; "
                f"set AS_EXECUTION_ENABLED=1 to arm it"
            )
            raise ConfigurationError(msg)
        if self.environment is not DeploymentEnvironment.LOCAL and not self.allow_bedrock_calls:
            msg = (
                f"a {self.environment.value} deployment may not advance a run without "
                f"ALLOW_BEDROCK_CALLS=1; refusing to record fabricated generations "
                f"against a deployed run"
            )
            raise ConfigurationError(msg)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AwsSettings:
        """Resolve deployment settings from the environment, failing closed.

        Raises:
            ConfigurationError: A required variable is absent, or a value is outside
                its vocabulary or range.
        """
        source = environ if env is None else env
        try:
            return cls.model_validate(
                {
                    "environment": _environment(source),
                    "table_name": source.get("AS_TABLE_NAME", "").strip(),
                    "run_id": source.get("AS_PILOT_RUN_ID", "").strip(),
                    "export_bucket": source.get("AS_EXPORT_BUCKET", "").strip() or None,
                    "event_bus_name": source.get("AS_EVENT_BUS_NAME", "").strip() or None,
                    "execution_enabled": _flag(source, "AS_EXECUTION_ENABLED"),
                    "allow_bedrock_calls": _flag(source, "ALLOW_BEDROCK_CALLS"),
                    "maximum_cycles": _optional_int(source, "AS_MAX_CYCLES"),
                    "allowed_origins": _origins(source),
                    "lock_ttl_seconds": _optional_int(source, "AS_LOCK_TTL_SECONDS") or 300,
                    "canonical": _flag(source, "AS_CANONICAL"),
                }
            )
        except ValueError as exc:
            raise ConfigurationError(str(exc)) from exc


def _environment(source: Mapping[str, str]) -> DeploymentEnvironment:
    raw = source.get("AS_DEPLOYMENT_ENVIRONMENT", DeploymentEnvironment.LOCAL.value)
    try:
        return DeploymentEnvironment(raw.strip().lower())
    except ValueError as exc:
        known = ", ".join(member.value for member in DeploymentEnvironment)
        msg = f"AS_DEPLOYMENT_ENVIRONMENT must be one of: {known} (got {raw!r})"
        raise ConfigurationError(msg) from exc


def _flag(source: Mapping[str, str], variable: str) -> bool:
    """Read a switch that must be turned on deliberately.

    Only the exact strings ``1`` and ``true`` arm it. Not ``yes``, not ``on``, not a
    non-empty string: every one of those turns a typo into an armed deployment.
    """
    return source.get(variable, "").strip().lower() in {"1", "true"}


def _optional_int(source: Mapping[str, str], variable: str) -> int | None:
    raw = source.get(variable, "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError as exc:
        msg = f"{variable} must be an integer (got {raw!r})"
        raise ConfigurationError(msg) from exc


def _origins(source: Mapping[str, str]) -> tuple[str, ...]:
    raw = source.get("AS_ALLOWED_ORIGINS", "").strip()
    return tuple(origin.strip() for origin in raw.split(",") if origin.strip())
