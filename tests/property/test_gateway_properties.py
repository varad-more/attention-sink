"""Invariants of what reaches a model, over generated memory sets.

The unit suite checks prompts a person wrote. These check what must hold for any
active set at all: that a label always resolves to the memory it stood for, that a
real identifier never appears, and that ordinary writing never trips the guard whose
false positives would take a run down for nothing.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from attention_sink.domain import ArmId, MemoryKind, MemoryState
from attention_sink.model_gateway import (
    PromptLeakError,
    PromptLibrary,
    assert_policy_blind,
    build_writer_request,
    present_memories,
)

GENERATED = settings(max_examples=200, deadline=None, suppress_health_check=[HealthCheck.too_slow])

LIBRARY = PromptLibrary()

_WORDS = (
    "lighthouse",
    "storm",
    "ferry",
    "tide",
    "grandmother",
    "roof",
    "brass",
    "gulls",
    "morning",
    "winter",
    "pier",
    "key",
    "drawer",
    "spine",
    "shed",
    "evening",
)
"""A vocabulary of ordinary words, none of which names a mechanism.

Unrestricted text would eventually generate "fifo" or "lru" and fail the guard,
which would be the guard working rather than a bug -- and would prove nothing about
whether ordinary writing survives it.
"""

sentences = st.lists(st.sampled_from(_WORDS), min_size=1, max_size=25).map(
    lambda words: " ".join(words) + "."
)


@st.composite
def active_sets(draw: st.DrawFn) -> MemoryState:
    """A state of one to eight neutral memories for a randomly chosen arm."""
    arm = draw(st.sampled_from(list(ArmId)))
    texts = draw(st.lists(sentences, min_size=1, max_size=8, unique=True))
    state = MemoryState(run_id="run_property", arm_id=arm)
    for index, text in enumerate(texts):
        state = state.admit(
            [
                state.mint(
                    text=text,
                    token_count=max(len(text.split()), 1),
                    memory_kind=MemoryKind.SEED,
                    cycle=index,
                )
            ]
        )
    return state


@GENERATED
@given(state=active_sets(), stimulus=sentences, cycle=st.integers(min_value=0, max_value=500))
def test_a_prompt_never_carries_a_real_identifier_or_an_arm(
    state: MemoryState, stimulus: str, cycle: int
) -> None:
    request = build_writer_request(
        LIBRARY, cycle=cycle, stimulus_text=stimulus, active_memories=state.active_memories
    )

    rendered = f"{request.system}\n{request.user}"
    assert state.arm_id.value not in rendered
    assert all(memory.memory_id not in rendered for memory in state.active_memories)


@GENERATED
@given(state=active_sets())
def test_every_label_resolves_to_the_memory_it_stood_for(state: MemoryState) -> None:
    presentation = present_memories(state.active_memories)

    for ref, memory in zip(presentation.refs, state.active_memories, strict=True):
        assert presentation.resolve(ref) == memory.memory_id
        assert presentation.text_for(ref) == memory.text
    assert presentation.resolve_all(presentation.refs) == state.active_memory_ids


@GENERATED
@given(state=active_sets(), stimulus=sentences)
def test_ordinary_writing_never_trips_the_blindness_guard(
    state: MemoryState, stimulus: str
) -> None:
    """A guard broad enough to fire on a memory would take a run down for nothing."""
    assert_policy_blind(stimulus, where="stimulus")
    for memory in state.active_memories:
        assert_policy_blind(memory.text, where="memory")


@GENERATED
@given(state=active_sets(), stimulus=sentences, arm=st.sampled_from(list(ArmId)))
def test_an_arm_identifier_anywhere_in_the_data_stops_the_prompt(
    state: MemoryState, stimulus: str, arm: ArmId
) -> None:
    with pytest.raises(PromptLeakError):
        build_writer_request(
            LIBRARY,
            cycle=1,
            stimulus_text=f"{stimulus} {arm.value}",
            active_memories=state.active_memories,
        )


@GENERATED
@given(state=active_sets(), stimulus=sentences, cycle=st.integers(min_value=0, max_value=500))
def test_the_prompt_hash_is_a_function_of_the_request(
    state: MemoryState, stimulus: str, cycle: int
) -> None:
    def build(at: int) -> str:
        return build_writer_request(
            LIBRARY, cycle=at, stimulus_text=stimulus, active_memories=state.active_memories
        ).prompt_hash

    assert build(cycle) == build(cycle)
    assert build(cycle) != build(cycle + 1)
