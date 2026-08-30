"""Identifier, version, and timestamp aliases used throughout the experiment.

Every identifier that reaches the ledger is constrained here rather than at each
use site, so an identifier that would be ambiguous in a URL, a filename, or a JSON
key cannot enter the system in the first place.

These are constrained aliases rather than :func:`typing.NewType` wrappers. A
``NewType`` would let mypy catch a ``MemoryId`` passed where a ``RunId`` belongs,
but it cannot carry the Pydantic string constraints that keep malformed values out
of persisted records. Validation at the boundary is worth more than nominal
typing, and every model below names its fields explicitly enough that the
substitution is visible in review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from annotated_types import Ge
from pydantic import AfterValidator, StringConstraints

__all__ = [
    "IDENTIFIER_PATTERN",
    "VERSION_PATTERN",
    "CycleNumber",
    "EventId",
    "Identifier",
    "MemoryId",
    "PromptVersion",
    "ProtocolVersion",
    "RunId",
    "StimulusId",
    "UtcTimestamp",
    "Version",
    "version_token",
]

IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"
"""Identifiers are ASCII, start alphanumeric, and are safe in a path or JSON key."""

VERSION_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$"
"""Version strings are opaque but must be comparable as exact strings."""

Identifier = Annotated[str, StringConstraints(strip_whitespace=True, pattern=IDENTIFIER_PATTERN)]
Version = Annotated[str, StringConstraints(strip_whitespace=True, pattern=VERSION_PATTERN)]

RunId = Identifier
"""Identifies one immutable experimental run. Forks always take a new one."""

MemoryId = Identifier
"""Unique within a run. Derived from the arm and the arm-local creation sequence."""

StimulusId = Identifier
"""Identifies the ordered stimulus that every arm receives in a given cycle."""

EventId = Identifier
"""Identifies one append-only ledger entry."""

PromptVersion = Version
"""Version of the prompt set a cycle was generated under."""

ProtocolVersion = Version
"""Version of the experimental protocol a run is executing."""

CycleNumber = Annotated[int, Ge(0)]
"""Zero-based index of a cycle within a run."""


def version_token(text: str) -> str:
    """Render an arbitrary identifier as a :data:`Version`.

    A vendor's model identifier is not a version string: ``amazon.nova-lite-v1:0``
    carries a colon, and an inference profile carries slashes, neither of which
    :data:`VERSION_PATTERN` admits. They still have to reach the records that say
    which evaluator produced a score, so they are transliterated rather than hashed:
    every rejected character becomes ``-``, which keeps the identifier legible to a
    reader who has to match it against a run manifest.

    Deterministic and lossy in one direction only. Two different model identifiers
    could in principle collapse to the same token, so the manifest keeps the
    identifier verbatim as well; this is the form that fits in a version field, not a
    replacement for recording what was used.

    Raises:
        ValueError: ``text`` is empty, or contains no character a version may start
            with. Refused rather than padded: a version nobody can trace back is
            worse than a missing one.
    """
    rendered = "".join(character if _is_version_safe(character) else "-" for character in text)
    trimmed = rendered.lstrip("-")[:64]
    if not trimmed:
        msg = f"{text!r} has no character a version string may be built from"
        raise ValueError(msg)
    return trimmed


def _is_version_safe(character: str) -> bool:
    return character.isascii() and (character.isalnum() or character in "._+-")


def _require_utc(value: datetime) -> datetime:
    """Reject naive timestamps and normalise everything else to UTC."""
    if value.tzinfo is None:
        msg = "timestamps must be timezone-aware; a naive time is unorderable across hosts"
        raise ValueError(msg)
    return value.astimezone(UTC)


UtcTimestamp = Annotated[datetime, AfterValidator(_require_utc)]
"""A timezone-aware instant, stored in UTC."""
