"""No prompt may name the mechanism under study.

ADR-004 is the reason the six arms are comparable at all. A writer told it forgets
would perform forgetting; a judge told which arm it is scoring would score its own
expectation. Neither failure crashes anything, so it has to be caught here.
"""

from __future__ import annotations

import json

import pytest

from attention_sink.domain import CANONICAL_ARMS, REFERENCE_ARMS, ArmId, MemoryKind, MemoryState
from attention_sink.model_gateway import (
    AuditOutput,
    EvaluationOutput,
    InterviewOutput,
    PromptLeakError,
    PromptLibrary,
    SummaryOutput,
    ThoughtOutput,
    assert_policy_blind,
    build_writer_request,
)
from attention_sink.model_gateway.rendering import BANNED_POLICY_VERSIONS
from attention_sink.policies import policy_for
from tests.factories import world_state

SCHEMAS = (ThoughtOutput, AuditOutput, SummaryOutput, InterviewOutput, EvaluationOutput)


@pytest.fixture
def library() -> PromptLibrary:
    return PromptLibrary()


def test_every_shipped_prompt_is_blind(library: PromptLibrary):
    for template in library.manifest():
        assert_policy_blind(template.system, where=template.identifier)
        assert_policy_blind(template.user_template, where=template.identifier)


def test_every_output_schema_is_blind():
    """Field descriptions reach the model as part of the tool schema."""
    for schema in SCHEMAS:
        assert_policy_blind(json.dumps(schema.model_json_schema()), where=schema.__name__)


@pytest.mark.parametrize("arm", list(ArmId), ids=[a.value for a in ArmId])
def test_the_guard_catches_every_arm_identifier(arm: ArmId):
    with pytest.raises(PromptLeakError, match=arm.value):
        assert_policy_blind(f"you are the {arm.value} agent", where="test")


@pytest.mark.parametrize("arm", [*CANONICAL_ARMS, *REFERENCE_ARMS], ids=lambda a: a.value)
def test_the_banned_version_list_covers_every_registered_policy(arm: ArmId):
    """The gateway cannot import the policies, so the list is checked against them here.

    A new mechanism whose version string nobody added to the gateway would otherwise
    be free to appear in a prompt.
    """
    assert policy_for(arm).policy_version in BANNED_POLICY_VERSIONS


@pytest.mark.parametrize("version", BANNED_POLICY_VERSIONS)
def test_the_guard_catches_every_policy_version(version: str):
    with pytest.raises(PromptLeakError):
        assert_policy_blind(f"decided under {version}", where="test")


@pytest.mark.parametrize(
    "phrase",
    [
        "we use a FIFO queue",
        "an LRU cache holds it",
        "the heavy-hitter set",
        "this is an attention sink",
        "a sliding window of memory",
        "the memory policy says",
        "moved to the graveyard",
        "its retention density fell",
    ],
)
def test_the_guard_catches_mechanism_vocabulary(phrase: str):
    with pytest.raises(PromptLeakError):
        assert_policy_blind(phrase, where="test")


def test_the_guard_does_not_fire_on_ordinary_writing():
    assert_policy_blind(
        "The kitchen sink was full and I let the water run while I thought about randomness.",
        where="test",
    )


def test_the_failure_names_the_token_and_never_the_text():
    recollection = "a private recollection nobody should see in a log"

    with pytest.raises(PromptLeakError) as excinfo:
        assert_policy_blind(f"{recollection}, decided by arm_lru", where="writer prompt")

    message = str(excinfo.value)
    assert "arm_lru" in message
    assert "writer prompt" in message
    assert recollection not in message


def test_a_memory_that_names_a_mechanism_stops_the_prompt(library: PromptLibrary):
    state = MemoryState(run_id="run_test", arm_id=ArmId.ARM_LRU)
    state = state.admit(
        [
            state.mint(
                text="Someone told me my memory works least-recently-used.",
                token_count=8,
                memory_kind=MemoryKind.SEED,
                cycle=0,
            )
        ]
    )

    with pytest.raises(PromptLeakError):
        build_writer_request(
            library, cycle=1, stimulus_text="a bell rings", active_memories=state.active_memories
        )


@pytest.mark.parametrize("arm", list(ArmId), ids=[a.value for a in ArmId])
def test_a_rendered_writer_prompt_never_names_the_arm_it_serves(library: PromptLibrary, arm: ArmId):
    """Real memory identifiers embed the arm, so they must not reach the prompt."""
    state = world_state(arm, count=3)

    request = build_writer_request(
        library,
        cycle=7,
        stimulus_text="a ship's bell in the fog",
        active_memories=state.active_memories,
    )

    rendered = f"{request.system}\n{request.user}"
    assert arm.value not in rendered
    assert "mem_" not in rendered
    for memory in state.active_memories:
        assert memory.memory_id not in rendered
