"""The documented prompt digests are the digests of the shipped prompts.

A version's prompt file is immutable: a run records the digest it used, and editing a
file in place would make every recorded digest refer to something that no longer
exists. Documenting the digests is only worth anything if the document is checked, so
it is checked here.
"""

from __future__ import annotations

import re
from pathlib import Path

from attention_sink.model_gateway import PromptLibrary, PromptName

DOCUMENT = Path(__file__).resolve().parents[2] / "docs" / "model-gateway.md"

# Table cells are padded by the repository formatter, so the whitespace is part of
# what a reader sees and none of what the digest means.
_ROW = re.compile(r"^\|\s*`([a-z-]+)/(v[0-9]+)`\s*\|\s*`(sha256:[0-9a-f]{64})`\s*\|$", re.MULTILINE)
_SET_DIGEST = re.compile(
    r"^Prompt set digest \(`(v[0-9]+)`\): `(sha256:[0-9a-f]{64})`$", re.MULTILINE
)


def documented() -> dict[tuple[str, str], str]:
    return {(name, version): digest for name, version, digest in _ROW.findall(DOCUMENT.read_text())}


def test_every_prompt_is_documented():
    rows = documented()

    assert {name for name, _version in rows} == {prompt.value for prompt in PromptName}


def test_every_documented_digest_matches_the_shipped_file():
    library = PromptLibrary()

    for (name, version), digest in documented().items():
        assert library.load(PromptName(name), version).digest == digest, (
            f"{name}/{version} was edited without a version bump, or the document is stale"
        )


def test_the_documented_prompt_set_digest_matches():
    match = _SET_DIGEST.search(DOCUMENT.read_text())

    assert match is not None, "the document must record a prompt set digest"
    version, digest = match.groups()
    assert PromptLibrary().prompt_set_digest(version) == digest
