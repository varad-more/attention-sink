"""The model-call ceiling, checked before anything is spent."""

from __future__ import annotations

import pytest

from attention_sink.model_gateway import CallMetadata, CallOutcome, ModelErrorCode, ModelRole
from attention_sink.pilot import ModelCallBudget, ModelCallBudgetExceeded
from attention_sink.pilot.protocol import ModelCallLimits

LIMITS = ModelCallLimits(
    writer_calls_per_cycle=6,
    summary_calls_per_cycle=2,
    evaluator_calls_per_cycle=0,
    interview_calls_per_cycle=0,
    interview_calls_per_checkpoint=6,
    max_model_calls_per_run=400,
)


def metadata(role: ModelRole, **overrides: object) -> CallMetadata:
    fields: dict[str, object] = {
        "role": role,
        "model_id": "fixture-model-v1",
        "region": "local",
        "outcome": CallOutcome.SUCCESS,
        "latency_ms": 3,
        "retry_count": 0,
        "simulated": True,
        "input_tokens": 10,
        "output_tokens": 4,
    }
    return CallMetadata.model_validate(fields | overrides)


@pytest.fixture
def budget() -> ModelCallBudget:
    b = ModelCallBudget(limits=LIMITS)
    b.open_cycle(1)
    return b


def test_a_normal_cycle_allows_six_writers_and_no_more(budget: ModelCallBudget):
    for _ in range(6):
        budget.spend(ModelRole.WRITER)
    assert budget.remaining(ModelRole.WRITER) == 0
    with pytest.raises(ModelCallBudgetExceeded, match="6 writer call"):
        budget.spend(ModelRole.WRITER)


def test_a_normal_cycle_allows_two_dreamer_summaries(budget: ModelCallBudget):
    budget.spend(ModelRole.SUMMARIZER)
    budget.spend(ModelRole.SUMMARIZER)
    with pytest.raises(ModelCallBudgetExceeded, match="2 summarizer call"):
        budget.spend(ModelRole.SUMMARIZER)


@pytest.mark.parametrize("role", [ModelRole.EVALUATOR, ModelRole.INTERVIEWER])
def test_a_normal_cycle_may_not_evaluate_or_interview(budget: ModelCallBudget, role: ModelRole):
    assert budget.allowance(role) == 0
    with pytest.raises(ModelCallBudgetExceeded, match="0 "):
        budget.spend(role)


@pytest.mark.parametrize("role", [ModelRole.EMBEDDING, ModelRole.TOKEN_COUNTER])
def test_a_role_this_protocol_never_declares_has_no_allowance(
    budget: ModelCallBudget, role: ModelRole
):
    assert budget.allowance(role) == 0
    with pytest.raises(ModelCallBudgetExceeded):
        budget.spend(role)


def test_a_checkpoint_may_interview_every_arm(budget: ModelCallBudget):
    budget.open_cycle(12, checkpoint=True)
    for _ in range(6):
        budget.spend(ModelRole.INTERVIEWER)
    with pytest.raises(ModelCallBudgetExceeded):
        budget.spend(ModelRole.INTERVIEWER)


def test_opening_a_cycle_resets_the_allowance_but_not_the_run_total(budget: ModelCallBudget):
    for _ in range(6):
        budget.spend(ModelRole.WRITER)
    budget.open_cycle(2)
    assert budget.remaining(ModelRole.WRITER) == 6
    budget.spend(ModelRole.WRITER)
    assert budget.usage.total_calls == 7
    assert budget.usage.calls_by_role == {"writer": 7}


def test_the_run_ceiling_stops_a_run_that_would_outspend_it():
    small = ModelCallBudget(limits=LIMITS.model_copy(update={"max_model_calls_per_run": 3}))
    for cycle in range(1, 5):
        small.open_cycle(cycle)
        if cycle <= 3:
            small.spend(ModelRole.WRITER)
        else:
            with pytest.raises(ModelCallBudgetExceeded, match="limited to 3 model calls"):
                small.spend(ModelRole.WRITER)


def test_usage_records_failures_tokens_and_retries(budget: ModelCallBudget):
    budget.spend(ModelRole.WRITER)
    budget.record(metadata(ModelRole.WRITER, retry_count=2))
    budget.record(
        metadata(
            ModelRole.WRITER,
            outcome=CallOutcome.FAILURE,
            error_code=ModelErrorCode.THROTTLING,
            output_tokens=None,
        )
    )
    usage = budget.usage
    assert usage.total_calls == 1
    assert usage.failed_calls == 1
    assert usage.simulated_calls == 2
    assert usage.retries == 2
    assert usage.input_tokens == 20
    assert usage.output_tokens == 4
