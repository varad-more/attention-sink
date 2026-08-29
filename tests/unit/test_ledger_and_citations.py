"""Audit records: claims, verifications, ledger entries, and metric evidence."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue, ValidationError

from attention_sink.domain import (
    ArmId,
    CitationClaim,
    CitationSource,
    CycleSnapshot,
    CycleStatus,
    LedgerEvent,
    LedgerEventType,
    MetricEvidence,
    PolicyDecisionCode,
    VerifiedCitation,
    content_hash,
)
from attention_sink.policies import FifoPolicy
from tests.factories import (
    PROMPT_VERSION,
    PROTOCOL_VERSION,
    RUN_ID,
    STIMULUS_ID,
    budget,
    context,
    uniform_state,
)

NOW = datetime.now(UTC)


def test_a_claim_is_not_a_verification() -> None:
    claim = CitationClaim(
        run_id=RUN_ID,
        arm_id=ArmId.ARM_LRU,
        cycle=3,
        memory_id="mem_arm_lru_000000",
        claim_index=0,
        quoted_span="the light through the window",
    )
    assert not hasattr(claim, "updates_memory_state")


@pytest.mark.parametrize(
    ("source", "updates"),
    [
        (CitationSource.WRITER, True),
        (CitationSource.INTERVIEW, False),
        (CitationSource.EVALUATION, False),
    ],
)
def test_only_writer_citations_may_change_state(source: CitationSource, updates: bool) -> None:
    citation = VerifiedCitation(
        run_id=RUN_ID,
        arm_id=ArmId.ARM_LRU,
        cycle=3,
        memory_id="mem_arm_lru_000000",
        source=source,
        auditor_version="auditor-v1",
        evidence="the phrase appears verbatim",
    )
    assert citation.updates_memory_state is updates


def test_a_verification_without_evidence_is_refused() -> None:
    with pytest.raises(ValidationError):
        VerifiedCitation(
            run_id=RUN_ID,
            arm_id=ArmId.ARM_LRU,
            cycle=3,
            memory_id="mem_arm_lru_000000",
            source=CitationSource.WRITER,
            auditor_version="auditor-v1",
            evidence="",
        )


def test_a_ledger_event_carries_its_own_integrity_digest() -> None:
    payload: dict[str, JsonValue] = {"memory_ids": ["mem_arm_fifo_000000"], "tokens": 30}
    event = LedgerEvent(
        event_id="evt_000001",
        run_id=RUN_ID,
        sequence=7,
        event_type=LedgerEventType.POLICY_DECIDED,
        occurred_at=NOW,
        arm_id=ArmId.ARM_FIFO,
        cycle=4,
        payload=payload,
        payload_hash=content_hash(repr(sorted(payload.items()))),
        idempotency_key="cycle-4-arm_fifo-decide",
    )
    assert LedgerEvent.model_validate_json(event.model_dump_json()) == event


def test_a_ledger_event_rejects_a_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        LedgerEvent(
            event_id="evt_000001",
            run_id=RUN_ID,
            sequence=0,
            event_type=LedgerEventType.RUN_CREATED,
            occurred_at=datetime(2026, 8, 29, 9, 0),
            payload_hash="sha256:0",
        )


def test_metric_evidence_records_what_a_score_rests_on() -> None:
    evidence = MetricEvidence(
        run_id=RUN_ID,
        arm_id=ArmId.ARM_HEAVY,
        cycle=4,
        metric_name="identity_continuity",
        value=0.62,
        evaluator_version="judge-v3",
        calculation_version="calc-v2",
        cited_memory_ids=("mem_arm_heavy_000000", "mem_arm_heavy_000004"),
        rationale="the answer reuses two founding commitments verbatim",
        computed_at=NOW,
    )
    assert MetricEvidence.model_validate_json(evidence.model_dump_json()) == evidence


def test_metric_evidence_refuses_to_cite_the_same_memory_twice() -> None:
    with pytest.raises(ValidationError, match="more than once as evidence"):
        MetricEvidence(
            run_id=RUN_ID,
            arm_id=ArmId.ARM_HEAVY,
            cycle=4,
            metric_name="identity_continuity",
            value=0.62,
            evaluator_version="judge-v3",
            calculation_version="calc-v2",
            cited_memory_ids=("mem_arm_heavy_000000", "mem_arm_heavy_000000"),
            rationale="duplicated evidence",
            computed_at=NOW,
        )


def a_snapshot(**changes: object) -> CycleSnapshot:
    state = uniform_state(ArmId.ARM_FIFO, count=4, tokens=10)
    decision = FifoPolicy().rebalance(state, budget(25), context(ArmId.ARM_FIFO))
    after = state.apply(decision)
    base: dict[str, object] = {
        "run_id": RUN_ID,
        "arm_id": ArmId.ARM_FIFO,
        "cycle": context(ArmId.ARM_FIFO).cycle,
        "stimulus_id": STIMULUS_ID,
        "status": CycleStatus.COMMITTED,
        "protocol_version": PROTOCOL_VERSION,
        "prompt_version": PROMPT_VERSION,
        "policy_version": decision.policy_version,
        "active_memory_ids": after.active_memory_ids,
        "active_tokens": after.active_tokens,
        "budget_tokens": 25,
        "state_hash": after.state_hash,
        "decision": decision,
        "committed_at": NOW,
    }
    return CycleSnapshot(**{**base, **changes})  # type: ignore[arg-type]


def test_a_committed_snapshot_matches_its_decision() -> None:
    snapshot = a_snapshot()
    assert snapshot.active_memory_ids == snapshot.decision.kept_memory_ids
    assert CycleSnapshot.model_validate_json(snapshot.model_dump_json()) == snapshot


def test_a_snapshot_rejects_a_decision_from_another_cycle() -> None:
    with pytest.raises(ValidationError, match="carries another decision"):
        a_snapshot(cycle=99)


def test_a_committed_snapshot_rejects_an_active_set_the_decision_did_not_produce() -> None:
    with pytest.raises(ValidationError, match="does not match what the decision kept"):
        a_snapshot(active_memory_ids=("mem_arm_fifo_000000",))


def test_a_committed_snapshot_rejects_an_over_budget_active_set() -> None:
    with pytest.raises(ValidationError, match="over the"):
        a_snapshot(active_tokens=500)


def test_a_committed_snapshot_rejects_a_decision_that_still_awaits_a_summary() -> None:
    from attention_sink.domain import SummarizationConfig
    from attention_sink.policies import SummarizationPolicy

    state = uniform_state(ArmId.ARM_SUMMARY, count=5, tokens=10)
    engine = SummarizationPolicy(config=SummarizationConfig(summary_target_token_limit=8))
    pending = engine.rebalance(state, budget(30), context(ArmId.ARM_SUMMARY))
    assert pending.decision_code is PolicyDecisionCode.COMPRESSION_PLANNED
    with pytest.raises(ValidationError, match="still awaits a summary"):
        CycleSnapshot(
            run_id=RUN_ID,
            arm_id=ArmId.ARM_SUMMARY,
            cycle=pending.cycle,
            stimulus_id=STIMULUS_ID,
            status=CycleStatus.COMMITTED,
            protocol_version=PROTOCOL_VERSION,
            prompt_version=PROMPT_VERSION,
            policy_version=pending.policy_version,
            active_memory_ids=state.active_memory_ids,
            active_tokens=state.active_tokens,
            budget_tokens=30,
            state_hash=state.state_hash,
            decision=pending,
            committed_at=NOW,
        )
