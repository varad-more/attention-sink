"""Invariants of the pilot's pure parts, over generated inputs."""

from __future__ import annotations

from hypothesis import given, settings
from hypothesis import strategies as st

from attention_sink.domain import ArmId, MemoryKind, MemoryState
from attention_sink.pilot import canonical_digest, canonical_json, validate_claims
from attention_sink.pilot.cli import BUDGET_ROUNDING, proposed_budget
from attention_sink.pilot.protocol import document_digest

RUN = "run_prop"


def state_with(count: int) -> MemoryState:
    """An arm holding ``count`` active memories, built the way the engine builds one."""
    state = MemoryState(run_id=RUN, arm_id=ArmId.ARM_FIFO)
    for index in range(count):
        state = state.admit(
            [
                state.mint(
                    text=f"memory {index}",
                    token_count=3,
                    memory_kind=MemoryKind.SEED,
                    cycle=0,
                )
            ]
        )
    return state


HELD = state_with(8)
IDS = list(HELD.active_memory_ids)


@given(claims=st.lists(st.sampled_from(IDS), max_size=20))
@settings(max_examples=250)
def test_validation_partitions_the_claims_it_was_given(claims: list[str]):
    accepted, rejected = validate_claims(HELD, claims)
    assert len(accepted) + len(rejected) == len(claims)
    assert set(accepted) <= set(claims)
    assert {r.memory_id for r in rejected} <= set(claims)


@given(claims=st.lists(st.sampled_from(IDS), max_size=20))
@settings(max_examples=250)
def test_no_memory_is_ever_counted_twice(claims: list[str]):
    accepted, _ = validate_claims(HELD, claims)
    assert len(set(accepted)) == len(accepted)


@given(claims=st.lists(st.sampled_from(IDS), max_size=20))
@settings(max_examples=250)
def test_accepted_claims_keep_the_order_they_were_made_in(claims: list[str]):
    accepted, _ = validate_claims(HELD, claims)
    first_seen = list(dict.fromkeys(claims))
    assert list(accepted) == first_seen


@given(claims=st.lists(st.text(min_size=1, max_size=12), max_size=10))
@settings(max_examples=250)
def test_a_memory_this_arm_never_held_is_never_accepted(claims: list[str]):
    accepted, rejected = validate_claims(HELD, claims)
    unknown = [c for c in claims if c not in IDS]
    assert not set(accepted) & set(unknown)
    assert all(r.reason == "not_active" for r in rejected if r.memory_id in unknown)


@given(
    payload=st.dictionaries(
        st.text(max_size=8),
        st.integers() | st.text(max_size=8) | st.booleans(),
        max_size=8,
    ),
    seed=st.integers(min_value=0, max_value=1_000_000),
)
@settings(max_examples=250)
def test_canonical_serialisation_does_not_depend_on_key_order(
    payload: dict[str, object], seed: int
):
    items = list(payload.items())
    rotated = dict(items[seed % (len(items) or 1) :] + items[: seed % (len(items) or 1)])
    assert rotated == payload
    assert canonical_json(rotated) == canonical_json(payload)
    assert canonical_digest(rotated) == canonical_digest(payload)


@given(
    payload=st.dictionaries(st.text(max_size=8), st.integers(), max_size=6),
    left=st.text(max_size=16),
    right=st.text(max_size=16),
)
@settings(max_examples=250)
def test_a_document_digest_ignores_only_its_own_hash_field(
    payload: dict[str, int], left: str, right: str
):
    assert document_digest({**payload, "content_hash": left}) == document_digest(
        {**payload, "content_hash": right}
    )


@given(seed_tokens=st.integers(min_value=1, max_value=100_000))
@settings(max_examples=250)
def test_the_budget_always_exceeds_the_seed_set_and_is_a_round_number(seed_tokens: int):
    budget = proposed_budget(seed_tokens)
    assert budget > seed_tokens
    assert budget % BUDGET_ROUNDING == 0


@given(
    smaller=st.integers(min_value=1, max_value=50_000),
    extra=st.integers(min_value=0, max_value=50_000),
)
@settings(max_examples=250)
def test_a_larger_seed_set_never_gets_a_smaller_budget(smaller: int, extra: int):
    assert proposed_budget(smaller + extra) >= proposed_budget(smaller)
