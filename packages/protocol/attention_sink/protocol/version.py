"""Version identity stamped onto every run manifest, event, and API response.

A result is only defensible if you can say what produced it. These four values are
the minimum needed to reconstruct that: the shape of the data, the rules of the
experiment, the build of the software, and the exact source it came from.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _distribution_version
from os import environ
from typing import Final

__all__ = [
    "PROTOCOL_VERSION",
    "SCHEMA_VERSION",
    "VersionInfo",
    "current_version",
]

SCHEMA_VERSION: Final[int] = 1
"""Shape of every persisted entity.

Bumped only when a stored record's structure changes in a way that a reader of the
previous version could misinterpret. Every entity carries this value so that a
replay knows which interpretation to apply.
"""

PROTOCOL_VERSION: Final[str] = "2026.08-draft"
"""Default identifier of the experimental protocol: prompts, budgets, thresholds.

Overridable by ``AS_PROTOCOL_VERSION`` because the canonical value is owned by the
versioned artefacts under ``experiments/protocols/``, not by application code. A
run that changes any part of the protocol must change this value; otherwise two
incomparable runs would claim to be the same experiment.
"""

_UNKNOWN_APP_VERSION: Final[str] = "0.0.0+unknown"


@dataclass(frozen=True, slots=True)
class VersionInfo:
    """The version identity of one running process."""

    schema_version: int
    protocol_version: str
    app_version: str
    git_commit: str | None

    def as_manifest_fields(self) -> dict[str, str | int | None]:
        """Render as the flat mapping embedded in run manifests and event envelopes."""
        return {
            "schema_version": self.schema_version,
            "protocol_version": self.protocol_version,
            "app_version": self.app_version,
            "git_commit": self.git_commit,
        }


def _app_version() -> str:
    """Return the installed distribution version, or a marked placeholder.

    A process running from an uninstalled checkout is a legitimate local-development
    state, so this degrades to an explicitly unknown value rather than failing --
    but it degrades *visibly*, so an unknown version can never be mistaken for a
    real release in a manifest.
    """
    try:
        return _distribution_version("attention-sink")
    except PackageNotFoundError:
        return _UNKNOWN_APP_VERSION


def current_version(env: Mapping[str, str] | None = None) -> VersionInfo:
    """Resolve the version identity of this process.

    Args:
        env: Environment to read. Defaults to the real process environment;
            injectable so that tests never mutate global state.
    """
    source = environ if env is None else env
    commit = source.get("AS_GIT_COMMIT", "").strip()
    return VersionInfo(
        schema_version=SCHEMA_VERSION,
        protocol_version=source.get("AS_PROTOCOL_VERSION", "").strip() or PROTOCOL_VERSION,
        app_version=_app_version(),
        git_commit=commit or None,
    )
