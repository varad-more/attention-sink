"""Prompts are files, they are versioned, and their digests are stable."""

from __future__ import annotations

from pathlib import Path

import pytest

from attention_sink.model_gateway import (
    PromptFormatError,
    PromptLibrary,
    PromptName,
    PromptNotFoundError,
)
from attention_sink.model_gateway.prompts import USER_SEPARATOR


@pytest.fixture
def library() -> PromptLibrary:
    return PromptLibrary()


def test_every_declared_prompt_exists_at_v1(library: PromptLibrary):
    manifest = library.manifest()

    assert [template.name for template in manifest] == list(PromptName)
    assert all(template.system and template.user_template for template in manifest)


def test_a_prompt_digest_is_stable_across_loads_and_libraries(library: PromptLibrary):
    first = library.load(PromptName.WRITER)
    second = PromptLibrary().load(PromptName.WRITER)

    assert first.digest == second.digest
    assert first.digest.startswith("sha256:")


def test_the_prompt_set_digest_covers_every_prompt(library: PromptLibrary, tmp_path: Path):
    """A byte-for-byte copy digests the same; one edited byte anywhere does not."""
    for template in library.manifest():
        directory = tmp_path / template.name.value
        directory.mkdir()
        source = library.root / template.name.value / "v1.txt"
        (directory / "v1.txt").write_bytes(source.read_bytes())

    assert PromptLibrary(tmp_path).prompt_set_digest() == library.prompt_set_digest()

    edited = tmp_path / PromptName.SUMMARIZER.value / "v1.txt"
    edited.write_text(edited.read_text(encoding="utf-8") + " ", encoding="utf-8")

    assert PromptLibrary(tmp_path).prompt_set_digest() != library.prompt_set_digest()


def test_loading_the_same_prompt_twice_returns_the_cached_template(library: PromptLibrary):
    assert library.load(PromptName.WRITER) is library.load(PromptName.WRITER)


def test_a_missing_version_names_the_path_it_looked_for(library: PromptLibrary):
    with pytest.raises(PromptNotFoundError, match="v9"):
        library.load(PromptName.WRITER, "v9")


def test_a_missing_prompt_directory_is_refused(tmp_path: Path):
    with pytest.raises(PromptNotFoundError, match="does not exist"):
        PromptLibrary(tmp_path / "absent")


def test_a_prompt_without_a_separator_is_refused(tmp_path: Path):
    (tmp_path / "writer").mkdir()
    (tmp_path / "writer" / "v1.txt").write_text("just instructions", encoding="utf-8")

    with pytest.raises(PromptFormatError, match="USER"):
        PromptLibrary(tmp_path).load(PromptName.WRITER)


def test_a_prompt_with_an_empty_turn_is_refused(tmp_path: Path):
    (tmp_path / "writer").mkdir()
    (tmp_path / "writer" / "v1.txt").write_text(
        f"system\n{USER_SEPARATOR}\n   \n", encoding="utf-8"
    )

    with pytest.raises(PromptFormatError, match="empty"):
        PromptLibrary(tmp_path).load(PromptName.WRITER)


def test_a_template_that_is_missing_a_field_names_the_field(library: PromptLibrary):
    template = library.load(PromptName.WRITER)

    with pytest.raises(PromptFormatError, match="stimulus"):
        template.render_user(cycle=1, fence="abc", memory_block="none")


def test_data_containing_a_dollar_sign_is_not_substituted(library: PromptLibrary):
    template = library.load(PromptName.WRITER)

    rendered = template.render_user(
        cycle=1, stimulus="it cost $fence and no more", fence="abc", memory_block="none"
    )

    assert "it cost $fence and no more" in rendered


def test_the_version_token_is_a_valid_domain_version(library: PromptLibrary):
    import re

    from attention_sink.domain.identifiers import VERSION_PATTERN

    for template in library.manifest():
        assert re.match(VERSION_PATTERN, template.version_token), template.version_token
