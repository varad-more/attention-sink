"""One complete cycle, locally, with no AWS account and no network.

This is the acceptance criterion "fixture mode provides a complete local cycle" as an
executable claim. It drives the Phase 2 memory kernel and the Phase 3 gateway
together: a thought is written, its citations are audited and folded into the arm's
statistics, the summarising policy plans a compression, the gateway writes the text
for it, the policy commits it, and the resulting state is interviewed, judged, and
embedded.

The wiring between the two is done here by hand. That orchestration is Phase 4's
subject; what this test establishes is that the two halves already fit.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from attention_sink.domain import (
    ArmId,
    CompressingMemoryPolicy,
    CycleContext,
    LineageRelation,
    Memory,
    MemoryKind,
    MemoryState,
    MemoryStatus,
    PolicyConfiguration,
    SummarizationConfig,
    TokenBudget,
)
from attention_sink.model_gateway import (
    SIMULATED_PREFIX,
    EvaluationTask,
    GatewaySettings,
    InterviewQuestion,
    ModelGateway,
    build_gateway,
)
from attention_sink.policies import policy_for
from tests.factories import WORLD_TEXTS

RUN_ID = "run_local"
ARM = ArmId.ARM_SUMMARY
CYCLE = 5
STIMULUS = "A ship's bell rings out somewhere in the fog."
QUESTIONS = [
    InterviewQuestion(question_id="origin", text="Where did you begin?"),
    InterviewQuestion(question_id="loss", text="What do you think you have lost?"),
]


@pytest.fixture
def gateway() -> ModelGateway:
    return build_gateway(GatewaySettings.from_env(env={}))


def must_get(state: MemoryState, memory_id: str) -> Memory:
    """Fetch a memory the caller has just established exists."""
    memory = state.get(memory_id)
    assert memory is not None, memory_id
    return memory


def seeded_state(gateway: ModelGateway) -> MemoryState:
    """Four seed memories, costed with the gateway's own counter."""
    state = MemoryState(run_id=RUN_ID, arm_id=ARM)
    for index, text in enumerate(WORLD_TEXTS[:4]):
        state = state.admit(
            [
                state.mint(
                    text=text,
                    token_count=gateway.token_counter.count(text),
                    memory_kind=MemoryKind.SEED,
                    cycle=index,
                )
            ]
        )
    return state


def test_a_complete_cycle_runs_locally_and_stays_within_budget(gateway: ModelGateway):
    state = seeded_state(gateway)
    counter = gateway.token_counter
    budget = TokenBudget(max_active_tokens=state.active_tokens - 8, counter_version=counter.version)
    context = CycleContext(
        run_id=RUN_ID,
        arm_id=ARM,
        cycle=CYCLE,
        stimulus_id="stim_005",
        protocol_version="2026.08-test",
        prompt_version="v1",
        run_random_seed="seed-0123456789abcdef",
    )

    # 1. The writer sees this cycle's event and the active memories, and nothing else.
    thought = gateway.writer.write(
        cycle=CYCLE, stimulus_text=STIMULUS, active_memories=state.active_memories
    )
    assert thought.metadata.simulated is True
    assert SIMULATED_PREFIX in thought.output.journal_entry

    # 2. The audit decides which of the claimed citations actually count.
    audit = gateway.auditor.audit(
        journal_entry=thought.output.journal_entry,
        candidate_memory=thought.output.candidate_memory,
        claims=thought.output.claimed_citations,
        active_memories=state.active_memories,
    )
    verified = audit.as_verified_citations(run_id=RUN_ID, arm_id=ARM, cycle=CYCLE)
    assert verified, "the fixture writer cites, so the fixture audit should sustain something"
    assert all(citation.updates_memory_state for citation in verified)

    # 3. Statistics move, then this cycle's thought is admitted.
    state = state.record_cycle_citations(verified, cycle=CYCLE, decay=0.9)
    assert must_get(state, verified[0].memory_id).citation_count == 1
    state = state.admit(
        [
            state.mint(
                text=thought.output.candidate_memory,
                token_count=counter.count(thought.output.candidate_memory),
                memory_kind=MemoryKind.GENERATED,
                cycle=CYCLE,
                source_stimulus_id="stim_005",
            )
        ]
    )
    assert state.active_tokens > budget.max_active_tokens, "the cycle must need a rebalance"

    # 4. The policy chooses what is compressed. The model is not asked.
    #
    # The summary ceiling is set well below the default here. At the default of 64 a
    # summary would cost more than the memories it replaced, so the arm would
    # correctly refuse to compress and fall back to eviction, and this test would be
    # exercising FIFO rather than the two-stage path it exists to cover.
    policy = policy_for(
        ARM,
        PolicyConfiguration(summarization=SummarizationConfig(summary_target_token_limit=8)),
    )
    assert isinstance(policy, CompressingMemoryPolicy)
    decision = policy.rebalance(state, budget, context)
    plan = decision.compression_plan
    assert plan is not None, "this state should require a compression"

    # 5. The gateway writes the text for the plan, and only for the plan.
    sources = [must_get(state, memory_id) for memory_id in plan.source_memory_ids]
    summary = gateway.summarizer.summarize(plan=plan, sources=sources)
    assert summary.summary_tokens <= plan.summary_target_token_limit
    assert summary.source_memory_ids == plan.source_memory_ids

    # 6. The policy commits it, charging the summary against the same budget.
    committed = policy.finalize_compression(
        state,
        budget,
        context,
        plan,
        state.mint(
            text=summary.output.summary_text,
            token_count=summary.summary_tokens,
            memory_kind=MemoryKind.SUMMARY,
            cycle=CYCLE,
            parent_memory_ids=plan.source_memory_ids,
        ),
    )
    assert committed.is_final
    state = state.apply(committed)
    assert state.active_tokens <= budget.max_active_tokens

    # 7. Lineage survives the compression, in both directions.
    compressed = [m for m in state.memories if m.status is MemoryStatus.COMPRESSED]
    assert {m.memory_id for m in compressed} == set(plan.source_memory_ids)
    edges = [e for e in state.lineage_edges if e.relation is LineageRelation.COMPRESSED_INTO]
    assert {e.parent_memory_id for e in edges} == set(plan.source_memory_ids)
    assert {e.child_memory_id for e in edges} == {plan.summary_memory_id}

    # 8. The interview is a measurement and changes nothing.
    before = state.state_hash
    interview = gateway.interviewer.interview(
        questions=QUESTIONS, active_memories=state.active_memories
    )
    assert [a.question_id for a in interview.output.answers] == ["origin", "loss"]
    assert set(interview.cited_memory_ids) <= set(state.active_memory_ids)
    assert state.state_hash == before

    # 9. A judgement may see what the arm has lost; a writer never may.
    judgement = gateway.evaluator.evaluate(
        task=EvaluationTask.GRAVEYARD_ECHO,
        passage=thought.output.journal_entry,
        reference_statements=["The writer grew up beside the sea."],
        records=state.memories,
    )
    evidence = judgement.as_metric_evidence(
        run_id=RUN_ID,
        arm_id=ARM,
        cycle=CYCLE,
        metric_name="graveyard_echo",
        computed_at=datetime(2026, 8, 29, tzinfo=UTC),
    )
    assert evidence.evaluator_version == "truth-evaluator.v1"
    assert evidence.calculation_version == "eval-calc-v1"

    # 10. Embeddings are produced once per model and content.
    first = gateway.embeddings.embed(thought.output.candidate_memory)
    again = gateway.embeddings.embed(thought.output.candidate_memory)
    assert first.deduplicated is False
    assert again.deduplicated is True

    # Nothing in this cycle may read as a real result.
    for metadata in (
        thought.metadata,
        audit.metadata,
        summary.metadata,
        interview.metadata,
        judgement.metadata,
        first.metadata,
    ):
        assert metadata.simulated is True


def test_the_same_local_cycle_twice_produces_the_same_text(gateway: ModelGateway):
    """A local run is reproducible, which is what makes it useful for development."""
    state = seeded_state(gateway)

    first = gateway.writer.write(
        cycle=CYCLE, stimulus_text=STIMULUS, active_memories=state.active_memories
    )
    second = build_gateway(GatewaySettings.from_env(env={})).writer.write(
        cycle=CYCLE, stimulus_text=STIMULUS, active_memories=state.active_memories
    )

    assert first.output == second.output
    assert first.metadata.prompt_hash == second.metadata.prompt_hash
