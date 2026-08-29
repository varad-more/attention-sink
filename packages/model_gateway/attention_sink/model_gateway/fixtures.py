"""Deterministic stand-in for model calls, for local development only.

Permitted by the project constitution solely behind explicit local configuration.
The guard in :class:`FixtureModelGateway` is not defensive programming for its own
sake: it is the mechanism that makes "no production endpoint silently returns mock
data" an enforced property rather than a convention.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from attention_sink.model_gateway.settings import ConfigurationError, RuntimeMode, RuntimeSettings

__all__ = ["FixtureFile", "FixtureModelGateway", "FixtureNotFoundError"]


class FixtureNotFoundError(LookupError):
    """No fixture exists for the requested task and key."""


class FixtureFile(BaseModel):
    """One task's canned responses, as stored under ``datasets/fixtures``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    task: str = Field(min_length=1)
    simulated: Literal[True] = True
    """Always true, and stored rather than assumed.

    The flag travels with the payload, so anything that copies a fixture response
    into an API result carries the marking with it.
    """

    responses: dict[str, dict[str, Any]] = Field(min_length=1)


class FixtureModelGateway:
    """Serves recorded responses keyed by task and fixture key.

    Deterministic by construction: the same key always yields the same bytes, which
    makes local runs reproducible and lets tests assert on exact output without a
    network call or an AWS account.
    """

    def __init__(self, settings: RuntimeSettings, root: Path) -> None:
        """Bind the gateway to a fixture directory, refusing to exist in production.

        Args:
            settings: Resolved runtime settings; must be in local mode.
            root: Directory holding ``<task>.json`` fixture files.

        Raises:
            ConfigurationError: Instantiated outside local mode, or ``root`` is not
                a directory.
        """
        if settings.mode is not RuntimeMode.LOCAL:
            msg = (
                f"fixture responses are available only in {RuntimeMode.LOCAL.value} "
                f"mode, not {settings.mode.value}"
            )
            raise ConfigurationError(msg)
        if not root.is_dir():
            msg = f"fixture directory does not exist: {root}"
            raise ConfigurationError(msg)
        self._root = root
        self._cache: dict[str, FixtureFile] = {}

    @property
    def simulated(self) -> bool:
        """Always true. Present so callers can treat this like any other gateway."""
        return True

    def load(self, task: str) -> FixtureFile:
        """Load and validate the fixture file for ``task``, caching the result.

        Raises:
            FixtureNotFoundError: No fixture file exists for the task.
        """
        cached = self._cache.get(task)
        if cached is not None:
            return cached
        path = self._root / f"{task}.json"
        if not path.is_file():
            msg = f"no fixture file for task {task!r} at {path}"
            raise FixtureNotFoundError(msg)
        fixture = FixtureFile.model_validate(json.loads(path.read_text(encoding="utf-8")))
        self._cache[task] = fixture
        return fixture

    def respond(self, task: str, key: str) -> dict[str, Any]:
        """Return the recorded response for ``task``/``key``.

        Raises:
            FixtureNotFoundError: The task or the key has no recorded response.
        """
        fixture = self.load(task)
        try:
            return fixture.responses[key]
        except KeyError as exc:
            available = ", ".join(sorted(fixture.responses))
            msg = f"fixture {task!r} has no key {key!r}; available keys: {available}"
            raise FixtureNotFoundError(msg) from exc
