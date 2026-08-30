"""Everything in this directory calls a real provider, and none of it runs by default.

Two switches, both required, and neither with a default. ``ALLOW_BEDROCK_CALLS=1``
says money may be spent; the five model identifiers say on what. A developer who
exports credentials and runs ``pytest`` gets the same skip CI gets, because a suite
that could quietly bill an account is a suite nobody should be able to start by
accident.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from attention_sink.model_gateway import (
    ConfigurationError,
    GatewaySettings,
    ModelGateway,
    ModelMode,
    build_gateway,
)

ALLOW = "ALLOW_BEDROCK_CALLS"

REQUIRED = (
    "AWS_REGION",
    "WRITER_MODEL_ID",
    "AUDITOR_MODEL_ID",
    "JUDGE_MODEL_ID",
    "SUMMARY_MODEL_ID",
    "EMBEDDING_MODEL_ID",
)


def _reason() -> str | None:
    """Why this suite is skipped, or None when it may run."""
    if os.environ.get(ALLOW, "").strip() not in {"1", "true"}:
        return f"{ALLOW} is not set; these tests invoke Bedrock and cost money"
    missing = [name for name in REQUIRED if not os.environ.get(name, "").strip()]
    if missing:
        return f"not configured: {', '.join(missing)}"
    return None


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Skip this directory unless it is explicitly armed.

    ``pytest_collection_modifyitems`` is a session hook: a conftest in a
    subdirectory is still handed *every* item pytest collected, not only the ones
    beneath it. Filtering by path is therefore not tidiness -- without it this
    function silently skips the entire suite, which is exactly what it did the first
    time it was written.
    """
    reason = _reason()
    if reason is None:
        return
    here = Path(__file__).parent
    for item in items:
        if item.path is not None and here in item.path.parents:
            item.add_marker(pytest.mark.skip(reason=reason))


@pytest.fixture(scope="session")
def bedrock_gateway() -> Iterator[ModelGateway]:
    """A gateway that really calls Bedrock, built from the process environment.

    Session-scoped so the token and embedding caches survive the suite: the point is
    to prove the calls work, not to pay for the same one six times.
    """
    settings = GatewaySettings.from_env()
    if settings.mode is not ModelMode.BEDROCK:
        msg = "the smoke suite requires MODEL_MODE=bedrock"
        raise ConfigurationError(msg)
    yield build_gateway(settings)
