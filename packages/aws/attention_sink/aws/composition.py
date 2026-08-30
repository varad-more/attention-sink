"""Where a deployed process decides what it is talking to.

The composition root, the same job ``scripts/local_cli.py`` does for the local
process. Nothing below this module chooses an adapter: the services hold protocols,
and this is the one place that says a protocol is satisfied by DynamoDB, by S3, and
by Bedrock rather than by SQLite, a directory, and a fixture.

Everything is built once per execution environment and reused across invocations. A
Lambda that rebuilt its clients on every call would pay a TLS handshake per cycle and
lose the token cache between them, which is the difference between a warm cycle and a
cold one. Nothing cached here holds per-invocation state: the repository is stateless
between calls and the service is rehydrated from the store on every advance.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

import boto3
from botocore.config import Config as BotocoreConfig

from attention_sink.analysis import AnalysisService
from attention_sink.aws.dynamodb import DynamoRepository
from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.aws.telemetry import StructuredLogger
from attention_sink.model_gateway import (
    ConfigurationError,
    GatewaySettings,
    ModelGateway,
    ModelMode,
    build_gateway,
)
from attention_sink.pilot.protocol import DEFAULT_PROTOCOL_ROOT, ProtocolBundle, load_bundle
from attention_sink.pilot.service import PilotService

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from mypy_boto3_dynamodb.client import DynamoDBClient
    from mypy_boto3_s3.client import S3Client

__all__ = ["Runtime", "build_runtime", "protocol_root", "reset_runtime"]

PROTOCOL_ROOT_ENV = "AS_PROTOCOL_ROOT"
"""Where the protocol files are, inside the deployment package."""

_CLIENT_CONFIG = BotocoreConfig(
    retries={"max_attempts": 3, "mode": "standard"},
    connect_timeout=5,
    read_timeout=30,
)
"""Transport settings for the storage and bus clients.

Botocore's retries are left on here, unlike the Bedrock client's. A throttled
``PutItem`` is a transport problem with no experimental consequence; a retried model
call is a second generation, which is why the gateway owns that one itself.
"""


def protocol_root() -> Path:
    """Where this process reads the protocol from.

    Inside a Lambda the deployment package is unpacked at ``LAMBDA_TASK_ROOT``; on a
    laptop the repository-relative default is right. Both are overridable, so a test
    can point at a copy without setting a Lambda variable.
    """
    configured = os.environ.get(PROTOCOL_ROOT_ENV, "").strip()
    if configured:
        return Path(configured)
    task_root = os.environ.get("LAMBDA_TASK_ROOT", "").strip()
    return Path(task_root) / DEFAULT_PROTOCOL_ROOT if task_root else DEFAULT_PROTOCOL_ROOT


@dataclass(frozen=True, slots=True)
class Runtime:
    """Everything a handler needs, already wired and already validated."""

    settings: AwsSettings
    gateway_settings: GatewaySettings
    bundle: ProtocolBundle
    gateway: ModelGateway
    repository: DynamoRepository
    logger: StructuredLogger

    def service(self) -> PilotService:
        """A cycle service over this runtime's store and gateway.

        Built per call rather than held, because it is cheap and because a service
        that outlived one invocation would be one more thing carrying state between
        two runs of a handler.
        """
        return PilotService(
            repository=self.repository,
            bundle=self.bundle,
            gateway=self.gateway,
            lock_ttl_seconds=self.settings.lock_ttl_seconds,
        )

    def analysis(self) -> AnalysisService:
        """An analysis service over this runtime's store and gateway."""
        return AnalysisService(repository=self.repository, bundle=self.bundle, gateway=self.gateway)

    def s3(self) -> S3Client:
        """A client for the export bucket."""
        return _session().client("s3", config=_CLIENT_CONFIG)

    def events(self) -> Any:
        """A client for the event bus the cycle-completed event is published on."""
        return _session().client("events", config=_CLIENT_CONFIG)


def _session() -> boto3.Session:
    """A session from the default credential chain.

    No profile, no key, no Region argument: in a Lambda these come from the execution
    role and the function's own Region, and anything else here would be a credential
    in source.
    """
    return boto3.Session()


def _dynamodb() -> DynamoDBClient:
    return _session().client("dynamodb", config=_CLIENT_CONFIG)


@lru_cache(maxsize=1)
def build_runtime(service_name: str) -> Runtime:
    """Resolve configuration and wire one process's dependencies.

    Cached for the life of the execution environment. ``service_name`` is part of the
    key so a test that builds two runtimes gets two, and so every log line says which
    function wrote it.

    Raises:
        ConfigurationError: A required variable is absent, the deployment is
            inconsistent, or a non-local deployment asked for fixture models.
    """
    settings = AwsSettings.from_env()
    gateway_settings = GatewaySettings.from_env()
    if (
        settings.environment is not DeploymentEnvironment.LOCAL
        and gateway_settings.mode is ModelMode.FIXTURE
    ):
        msg = (
            f"a {settings.environment.value} deployment cannot run with "
            f"MODEL_MODE=fixture; a deployed run must never record fabricated "
            f"generations against a real table"
        )
        raise ConfigurationError(msg)

    bundle = load_bundle(protocol_root())
    gateway = build_gateway(gateway_settings)
    models = gateway_settings.models
    repository = DynamoRepository(
        table_name=settings.table_name,
        client=_dynamodb(),
        embedding_model_id=(
            models.embedding_model_id if models is not None else "fixture-embedding"
        ),
        lock_ttl_seconds=settings.lock_ttl_seconds,
    )
    return Runtime(
        settings=settings,
        gateway_settings=gateway_settings,
        bundle=bundle,
        gateway=gateway,
        repository=repository,
        logger=StructuredLogger(
            service=service_name,
            environment=settings.environment.value,
            context={"run_id": settings.run_id},
        ),
    )


def reset_runtime() -> None:
    """Forget the cached runtime.

    For tests that change the environment between cases. Never called by a handler:
    a Lambda that dropped its clients mid-execution would pay for them again.
    """
    build_runtime.cache_clear()
