"""Production budgets are counted by the model, cached by content, and never guessed."""

from __future__ import annotations

import re

import pytest

from attention_sink.domain.identifiers import VERSION_PATTERN
from attention_sink.model_gateway import (
    BEDROCK_COUNTER_VERSION,
    CONVERSE_COUNTER_VERSION,
    BedrockTokenCounter,
    ConverseTokenCounter,
    ModelErrorCode,
    ModelInvocationError,
    ModelRole,
    Retrier,
    RetryPolicy,
)
from tests.doubles import FakeRuntime
from tests.unit.test_failures import client_error


def counter(runtime: FakeRuntime, *, retries: int = 1) -> BedrockTokenCounter:
    return BedrockTokenCounter(
        model_id="writer-model",
        region="eu-west-2",
        client=runtime,
        retrier=Retrier(policy=RetryPolicy(max_attempts=retries + 1), sleep=lambda _s: None),
    )


def test_the_counter_version_is_a_valid_domain_version():
    """It travels on every ``TokenBudget``, which constrains what a version may look like."""
    assert re.match(VERSION_PATTERN, BEDROCK_COUNTER_VERSION)


def test_the_descriptor_names_both_the_counter_and_the_model():
    subject = counter(FakeRuntime(token_counts=[7]))

    assert subject.descriptor == f"{BEDROCK_COUNTER_VERSION}+writer-model"


def test_text_is_counted_by_the_model():
    runtime = FakeRuntime(token_counts=[37])

    result = counter(runtime).count_detailed("a block of active memory")

    assert result.tokens == 37
    assert result.metadata.role is ModelRole.TOKEN_COUNTER
    assert result.metadata.model_id == "writer-model"
    assert result.metadata.request_id == "count-0"
    assert result.metadata.simulated is False
    assert runtime.count_requests[0]["input"] == {
        "converse": {
            "messages": [{"role": "user", "content": [{"text": "a block of active memory"}]}]
        }
    }


def test_a_complete_request_is_counted_with_both_turns():
    runtime = FakeRuntime(token_counts=[81])

    result = counter(runtime).count_request(system="instructions", user="the memories")

    assert result.tokens == 81
    assert runtime.count_requests[0]["input"]["converse"]["system"] == [{"text": "instructions"}]


def test_counts_are_cached_on_model_and_content():
    runtime = FakeRuntime(token_counts=[11, 999])
    subject = counter(runtime)

    first = subject.count("the same words")
    second = subject.count("the same words")
    other = subject.count("different words")

    assert first == second == 11
    assert other == 999
    assert len(runtime.count_requests) == 2
    assert subject.cached_count == 2


def test_two_counters_for_different_models_do_not_share_a_cache():
    runtime = FakeRuntime(token_counts=[11, 22])
    writer = counter(runtime)
    judge = BedrockTokenCounter(model_id="judge-model", region="eu-west-2", client=runtime)

    assert writer.count("the same words") == 11
    assert judge.count("the same words") == 22


def test_blank_text_costs_nothing_and_makes_no_call():
    runtime = FakeRuntime(token_counts=[5])

    result = counter(runtime).count_detailed("   \n ")

    assert result.tokens == 0
    assert runtime.count_requests == []


def test_a_failed_count_never_falls_back_to_an_estimate():
    """Silent degradation would leave a run's budget in a unit its manifest denies."""
    runtime = FakeRuntime(token_counts=[client_error("ThrottlingException")])

    with pytest.raises(ModelInvocationError) as excinfo:
        counter(runtime, retries=2).count("some text")

    assert excinfo.value.code is ModelErrorCode.THROTTLING
    assert excinfo.value.metadata.retry_count == 2
    assert len(runtime.count_requests) == 3


def test_a_transient_failure_is_retried_and_then_counted():
    runtime = FakeRuntime(token_counts=[client_error("InternalServerException", status=500), 19])

    result = counter(runtime, retries=2).count_detailed("some text")

    assert result.tokens == 19
    assert result.metadata.retry_count == 1


# ------------------------------------------------- the counter that costs a call


def converse_counter(runtime: FakeRuntime, *, retries: int = 1) -> ConverseTokenCounter:
    return ConverseTokenCounter(
        model_id="writer-model",
        region="eu-west-2",
        client=runtime,
        retrier=Retrier(policy=RetryPolicy(max_attempts=retries + 1), sleep=lambda _s: None),
    )


def test_the_converse_counter_version_is_a_valid_domain_version():
    """It travels on every ``TokenBudget`` a canonical run measures."""
    assert re.match(VERSION_PATTERN, CONVERSE_COUNTER_VERSION)
    assert CONVERSE_COUNTER_VERSION != BEDROCK_COUNTER_VERSION


def test_the_model_counts_its_own_input_through_an_invocation_capped_at_one_token():
    runtime = FakeRuntime(token_counts=[512])

    result = converse_counter(runtime).count_detailed("a block of active memory")

    assert result.tokens == 512
    assert result.metadata.role is ModelRole.TOKEN_COUNTER
    assert result.metadata.simulated is False
    assert result.metadata.request_id == "converse-0"
    request = runtime.converse_requests[0]
    assert request["modelId"] == "writer-model"
    assert request["messages"] == [
        {"role": "user", "content": [{"text": "a block of active memory"}]}
    ]
    assert request["inferenceConfig"] == {"maxTokens": 1, "temperature": 0.0}
    assert "system" not in request
    assert runtime.count_requests == []


def test_a_counted_request_carries_the_system_turn_it_will_really_be_sent_with():
    runtime = FakeRuntime(token_counts=[900])

    result = converse_counter(runtime).count_request(system="instructions", user="memories")

    assert result.tokens == 900
    request = runtime.converse_requests[0]
    assert request["system"] == [{"text": "instructions"}]
    assert request["messages"] == [{"role": "user", "content": [{"text": "memories"}]}]


def test_counting_by_invocation_costs_the_one_token_it_generates():
    """A billed count has to appear in the run's tally as the call it was."""
    result = converse_counter(FakeRuntime(token_counts=[64])).count_detailed("text")

    assert result.metadata.input_tokens == 64
    assert result.metadata.output_tokens == 1


def test_counting_by_count_tokens_generates_nothing():
    result = counter(FakeRuntime(token_counts=[64])).count_detailed("text")

    assert result.metadata.input_tokens == 64
    assert result.metadata.output_tokens == 0


def test_a_cached_count_reports_the_tokens_it_did_not_spend_as_zero():
    """The count is remembered; the cost is not repeated. See the tally in ModelUsage."""
    runtime = FakeRuntime(token_counts=[64])
    subject = converse_counter(runtime)

    first = subject.count_detailed("text")
    second = subject.count_detailed("text")

    assert (first.tokens, second.tokens) == (64, 64)
    assert first.metadata.input_tokens == 64
    assert second.metadata.input_tokens == 0
    assert second.metadata.output_tokens == 0
    assert len(runtime.converse_requests) == 1


def test_a_failed_invocation_count_never_falls_back_to_an_estimate():
    runtime = FakeRuntime(token_counts=[client_error("ThrottlingException")])

    with pytest.raises(ModelInvocationError) as raised:
        converse_counter(runtime, retries=0).count("text the budget needs a number for")

    assert raised.value.metadata.error_code is ModelErrorCode.THROTTLING
