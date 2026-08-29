"""Versioned prompt files, loaded once and digested.

Prompts are experimental apparatus. Two runs whose prompts differ are different
experiments, so every prompt lives in a file under a version directory, is loaded
verbatim, and carries a SHA-256 digest of its own bytes. A run records the digest it
used; a reader who doubts a result can check the prompt that produced it rather than
take the version string on trust.

Each file holds both turns of the conversation, separated by a marker line: the
static system instruction above it, and the data template below. Keeping both in one
versioned artefact is what ADR-004 requires -- a prompt assembled across handlers
cannot be reviewed as a whole, and a leak would be invisible in any single diff.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from string import Template

from attention_sink.domain import content_hash

__all__ = [
    "DEFAULT_PROMPT_VERSION",
    "PROMPT_ROOT",
    "USER_SEPARATOR",
    "PromptFormatError",
    "PromptLibrary",
    "PromptName",
    "PromptNotFoundError",
    "PromptTemplate",
]

PROMPT_ROOT = Path(__file__).parent / "prompt_templates"
"""Prompts ship inside the package rather than beside it.

A prompt that travelled separately from the code could be absent, stale, or a
different version in a deployed artefact, and the digest recorded in the manifest
would then describe a file nobody ran.
"""

USER_SEPARATOR = "--- USER ---"
"""The line that divides the static system instruction from the data template."""

DEFAULT_PROMPT_VERSION = "v1"


class PromptName(StrEnum):
    """The prompts this experiment uses. One directory each."""

    WRITER = "writer"
    CITATION_AUDITOR = "citation-auditor"
    SUMMARIZER = "summarizer"
    INTERVIEW = "interview"
    TRUTH_EVALUATOR = "truth-evaluator"
    SUMMARY_ENTAILMENT = "summary-entailment"


class PromptNotFoundError(LookupError):
    """No prompt file exists for the requested name and version."""


class PromptFormatError(ValueError):
    """A prompt file is missing its separator, or a field it asks for."""


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    """One prompt file: both turns, and the digest of the bytes they came from."""

    name: PromptName
    version: str
    system: str
    user_template: str
    digest: str
    """``sha256:`` digest of the whole file, separator included."""

    @property
    def identifier(self) -> str:
        """``name/version``, as it appears in a manifest and in metadata."""
        return f"{self.name.value}/{self.version}"

    @property
    def version_token(self) -> str:
        """The same identity, in the form the domain's ``Version`` alias accepts.

        ``MetricEvidence`` stores the evaluator version as a constrained string that
        excludes ``/``, so the separator changes rather than the identity.
        """
        return f"{self.name.value}.{self.version}"

    def render_user(self, **fields: object) -> str:
        """Substitute ``$field`` placeholders in the data turn.

        Only the template is scanned for placeholders, so a ``$`` inside supplied
        data is ordinary text and cannot introduce a substitution of its own.

        Raises:
            PromptFormatError: The template names a field that was not supplied.
        """
        try:
            return Template(self.user_template).substitute(fields)
        except KeyError as exc:
            msg = f"prompt {self.identifier} needs field {exc.args[0]!r}, which was not supplied"
            raise PromptFormatError(msg) from exc


class PromptLibrary:
    """Loads and caches the prompt files, and reports what it loaded.

    Caching is by name and version. A prompt file is immutable for the life of a
    version: editing one in place rather than adding ``v2`` would change what a
    recorded digest refers to, which is the one thing the digest exists to prevent.
    """

    def __init__(self, root: Path = PROMPT_ROOT) -> None:
        """Bind the library to a prompt directory.

        Args:
            root: Directory holding ``<name>/<version>.txt``. Injectable so tests
                can exercise malformed files without shipping one.

        Raises:
            PromptNotFoundError: ``root`` is not a directory.
        """
        if not root.is_dir():
            msg = f"prompt directory does not exist: {root}"
            raise PromptNotFoundError(msg)
        self._root = root
        self._cache: dict[tuple[PromptName, str], PromptTemplate] = {}

    @property
    def root(self) -> Path:
        """The directory this library reads from."""
        return self._root

    def load(self, name: PromptName, version: str = DEFAULT_PROMPT_VERSION) -> PromptTemplate:
        """Load and digest one prompt, caching the result.

        Raises:
            PromptNotFoundError: No file exists for this name and version.
            PromptFormatError: The file has no separator line, or an empty turn.
        """
        key = (name, version)
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        path = self._root / name.value / f"{version}.txt"
        if not path.is_file():
            msg = f"no prompt file for {name.value}/{version} at {path}"
            raise PromptNotFoundError(msg)

        text = path.read_text(encoding="utf-8")
        head, separator, tail = text.partition(f"{USER_SEPARATOR}\n")
        if not separator:
            msg = f"prompt {name.value}/{version} has no {USER_SEPARATOR!r} line"
            raise PromptFormatError(msg)
        if not head.strip() or not tail.strip():
            msg = f"prompt {name.value}/{version} has an empty system or data turn"
            raise PromptFormatError(msg)

        template = PromptTemplate(
            name=name,
            version=version,
            system=head.strip(),
            user_template=tail.strip(),
            digest=content_hash(text),
        )
        self._cache[key] = template
        return template

    def manifest(self, version: str = DEFAULT_PROMPT_VERSION) -> tuple[PromptTemplate, ...]:
        """Every prompt at ``version``, in declaration order.

        Raises:
            PromptNotFoundError: Any prompt is missing at that version.
        """
        return tuple(self.load(name, version) for name in PromptName)

    def prompt_set_digest(self, version: str = DEFAULT_PROMPT_VERSION) -> str:
        """One digest covering every prompt at ``version``.

        Recorded in the run manifest so a single value identifies the whole prompt
        set. Two runs whose prompt-set digests differ are not comparable, whatever
        their version strings say.
        """
        lines = "\n".join(f"{t.identifier}={t.digest}" for t in self.manifest(version))
        return content_hash(lines)
