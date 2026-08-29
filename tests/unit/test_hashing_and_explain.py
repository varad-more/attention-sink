"""Digests and explanation templates: the two places determinism is easy to lose."""

from __future__ import annotations

import pytest

from attention_sink.domain import (
    ArmId,
    PolicyDecisionCode,
    content_hash,
    render_explanation,
    selection_digest,
    state_hash,
)


def test_content_hash_is_stable_and_prefixed() -> None:
    assert content_hash("a memory") == content_hash("a memory")
    assert content_hash("a memory").startswith("sha256:")
    assert content_hash("a memory") != content_hash("a memory ")


def test_state_hash_depends_on_order_not_only_membership() -> None:
    assert state_hash(["a", "b"]) != state_hash(["b", "a"])
    assert state_hash([]) == state_hash([])


def test_state_hash_cannot_be_confused_by_a_separator_in_an_identifier() -> None:
    # The separator is excluded from IDENTIFIER_PATTERN, so this collision is
    # unreachable through validated data; asserting it keeps that guarantee honest.
    assert state_hash(["a|b", "c"]) != state_hash(["a", "b|c"])


def test_selection_digest_is_hex_and_reproducible() -> None:
    digest = selection_digest(
        run_random_seed="seed-0123456789abcdef",
        arm_id="arm_random",
        cycle=3,
        decision_index=0,
        candidate_memory_ids=["m2", "m1"],
    )
    assert len(digest) == 64
    assert int(digest, 16) >= 0
    assert digest == selection_digest(
        run_random_seed="seed-0123456789abcdef",
        arm_id="arm_random",
        cycle=3,
        decision_index=0,
        candidate_memory_ids=["m1", "m2"],
    )


@pytest.mark.parametrize(
    "field",
    ["run_random_seed", "arm_id", "cycle", "decision_index", "candidate_memory_ids"],
)
def test_every_field_changes_the_selection_digest(field: str) -> None:
    base: dict[str, object] = {
        "run_random_seed": "seed-0123456789abcdef",
        "arm_id": "arm_random",
        "cycle": 3,
        "decision_index": 0,
        "candidate_memory_ids": ["m1", "m2"],
    }
    changed = {
        "run_random_seed": "seed-fedcba9876543210",
        "arm_id": "arm_fifo",
        "cycle": 4,
        "decision_index": 1,
        "candidate_memory_ids": ["m1", "m3"],
    }
    assert selection_digest(**base) != selection_digest(**{**base, field: changed[field]})  # type: ignore[arg-type]


@pytest.mark.parametrize("code", list(PolicyDecisionCode))
def test_every_decision_code_has_a_template(code: PolicyDecisionCode) -> None:
    rendered = render_explanation(
        arm_id=ArmId.ARM_FIFO,
        cycle=4,
        code=code,
        budget_tokens=100,
        tokens_before=120,
        tokens_after=90,
        kept_memory_ids=("a", "b"),
        retired_memory_ids=("c",),
        eligible_count=3,
        compression_sources=2,
        summary_limit=8,
        summary_tokens=5,
        tokens_freed=30,
    )
    assert rendered.startswith("arm_fifo cycle 4: ")
    assert rendered.endswith(".")
    assert "{" not in rendered


def test_an_explanation_names_the_memories_it_retired() -> None:
    rendered = render_explanation(
        arm_id=ArmId.ARM_LRU,
        cycle=2,
        code=PolicyDecisionCode.EVICTED_OLDEST,
        budget_tokens=10,
        tokens_before=20,
        tokens_after=10,
        retired_memory_ids=("mem_a", "mem_b"),
        eligible_count=4,
    )
    assert "retired mem_a, mem_b" in rendered


def test_an_explanation_with_nothing_retired_names_nothing() -> None:
    rendered = render_explanation(
        arm_id=ArmId.ARM_LRU,
        cycle=2,
        code=PolicyDecisionCode.NO_ACTION_WITHIN_BUDGET,
        budget_tokens=10,
        tokens_before=8,
        tokens_after=8,
        kept_memory_ids=("mem_a",),
    )
    assert "retired" not in rendered
