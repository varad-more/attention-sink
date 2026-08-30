"""Configuration for a local run, resolved from the protocol and the gateway.

Deliberately does *not* choose a repository. Picking an adapter is composition, and
composition lives in ``scripts/local_cli.py``; if it lived here the pilot package
would import the SQLite adapter and the application would depend on its own
infrastructure. ``tests/unit/test_import_boundaries.py`` fails if that happens.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from attention_sink.model_gateway import ModelGateway
from attention_sink.pilot.cli import counter_identity, model_specs
from attention_sink.pilot.configuration import PilotRunConfiguration, RunKind
from attention_sink.pilot.protocol import ProtocolBundle
from attention_sink.protocol import current_version

__all__ = [
    "DEFAULT_DATABASE",
    "DEFAULT_EXPORT",
    "DEFAULT_RUN_ID",
    "SIMULATED_BANNER",
    "build_configuration",
]

DEFAULT_DATABASE = Path(".pilot-local/pilot.sqlite3")
DEFAULT_RUN_ID = "run_local_pilot"
DEFAULT_EXPORT = Path(".pilot-runs/dataset")

SIMULATED_BANNER = (
    "SIMULATED - LOCAL - NON-CANONICAL. Fixture models, local approximate token budget."
)


def build_configuration(
    bundle: ProtocolBundle,
    *,
    run_id: str,
    gateway: ModelGateway,
    run_kind: RunKind = RunKind.LOCAL_FIXTURE,
    now: datetime | None = None,
) -> PilotRunConfiguration:
    """Resolve the configuration one local run is defined by.

    Raises:
        ProtocolError: The protocol is not validated, has drifted, or is uncalibrated.
    """
    bundle.require_runnable(canonical=run_kind.is_canonical)
    writer, embedding = model_specs(gateway)
    counter_version, token_count_source = counter_identity(gateway)
    version = current_version()
    return PilotRunConfiguration.from_bundle(
        bundle,
        run_id=run_id,
        created_at=now or datetime.now(UTC),
        writer_model=writer,
        embedding_model=embedding,
        prompt_set_digest=gateway.prompts.prompt_set_digest(bundle.protocol.writer_prompt_version),
        app_version=version.app_version,
        git_commit=version.git_commit,
        run_kind=run_kind,
        counter_version=counter_version,
        token_count_source=token_count_source,
    )
