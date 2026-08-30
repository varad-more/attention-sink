"""Canonical serialisation, snapshot digests, and what a snapshot refuses to be."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import BaseModel

from attention_sink.domain import ArmId, MemoryStatus, PolicyDecisionCode
from attention_sink.pilot import (
    ArmCycleSnapshot,
    PilotEngine,
    RunStatus,
    canonical_digest,
    canonical_json,
)
from attention_sink.pilot.snapshots import RetiredMemoryRecord, StimulusRecord

STIMULUS = StimulusRecord(
    stimulus_id="stim_001",
    cycle=1,
    phase="orientation",
    reliability="reliable",
    text="The service ladder ends.",
)


class Nested(BaseModel):
    when: datetime
    status: RunStatus


# ------------------------------------------------------------------ canonical


def test_key_order_does_not_change_the_serialisation():
    assert canonical_json({"b": 1, "a": 2}) == canonical_json({"a": 2, "b": 1})
    assert canonical_json({"b": 1, "a": 2}) == '{"a":2,"b":1}'


def test_models_reduce_at_any_depth():
    inner = Nested(when=datetime(2026, 8, 29, tzinfo=UTC), status=RunStatus.RUNNING)
    rendered = canonical_json({"one": inner, "many": [inner, inner]})
    assert '"status":"running"' in rendered
    assert "2026-08-29T00:00:00Z" in rendered


def test_a_value_with_no_canonical_form_is_refused():
    with pytest.raises(TypeError, match="no canonical JSON form"):
        canonical_json({"bad": object()})


def test_unicode_survives_rather_than_being_escaped():
    assert canonical_json({"a": "Kestrel — 03:17"}) == '{"a":"Kestrel — 03:17"}'


def test_the_digest_is_stable_across_equal_values():
    assert canonical_digest({"a": [1, 2]}) == canonical_digest({"a": [1, 2]})
    assert canonical_digest({"a": [1, 2]}) != canonical_digest({"a": [2, 1]})
    assert canonical_digest({}).startswith("sha256:")


# ---------------------------------------------------------------- run snapshot


def test_a_run_snapshot_seals_and_verifies(pilot_engine):
    snapshot = pilot_engine.run_snapshot()
    assert snapshot.status is RunStatus.INITIALIZED
    assert snapshot.current_cycle == 0
    assert snapshot.verify_hash()
    assert set(snapshot.arm_states) == {arm.value for arm in pilot_engine.configuration.arms}


def test_an_edited_run_snapshot_no_longer_verifies(pilot_engine):
    snapshot = pilot_engine.run_snapshot()
    assert not snapshot.model_copy(update={"current_cycle": 3}).verify_hash()


def test_a_run_snapshot_must_hold_every_configured_arm(pilot_engine):
    snapshot = pilot_engine.run_snapshot()
    states = dict(snapshot.arm_states)
    states.pop(ArmId.ARM_FIFO.value)
    with pytest.raises(ValueError, match="but the run configures"):
        snapshot.model_copy(update={"arm_states": states}).model_validate(
            snapshot.model_copy(update={"arm_states": states}).model_dump()
        )


# --------------------------------------------------------------- arm snapshot


def committed(engine: PilotEngine, cycle: int = 1) -> ArmCycleSnapshot:
    return engine.run_cycle(cycle)[0]


def test_an_arm_snapshot_seals_verifies_and_is_reproducible(pilot_engine):
    snapshot = committed(pilot_engine)
    assert snapshot.verify_hash()
    assert snapshot.snapshot_hash == canonical_digest(snapshot.unhashed_payload)


def test_changing_one_field_changes_the_hash(pilot_engine):
    snapshot = committed(pilot_engine)
    altered = snapshot.model_copy(update={"journal_entry": snapshot.journal_entry + "!"})
    assert not altered.verify_hash()
    assert altered.sealed().snapshot_hash != snapshot.snapshot_hash


def test_a_snapshot_over_its_budget_cannot_be_constructed(pilot_engine):
    snapshot = committed(pilot_engine)
    with pytest.raises(ValueError, match="over the"):
        snapshot.model_copy(update={"tokens_after": snapshot.budget_tokens + 1}).model_validate(
            {**snapshot.model_dump(), "tokens_after": snapshot.budget_tokens + 1}
        )


def test_a_snapshot_whose_kept_set_disagrees_with_its_decision_is_refused(pilot_engine):
    snapshot = committed(pilot_engine)
    payload = {**snapshot.model_dump(), "active_memory_ids_after": ()}
    with pytest.raises(ValueError, match="kept a different set"):
        ArmCycleSnapshot.model_validate(payload)


def test_summary_sources_without_a_summary_are_refused(pilot_engine):
    snapshot = committed(pilot_engine)
    payload = {**snapshot.model_dump(), "summary_source_memory_ids": ("mem_arm_fifo_000000",)}
    with pytest.raises(ValueError, match="names summary sources without a summary"):
        ArmCycleSnapshot.model_validate(payload)


def test_a_retirement_record_carries_the_text_that_was_lost():
    record = RetiredMemoryRecord(
        memory_id="mem_arm_fifo_000000",
        status=MemoryStatus.EVICTED,
        reason=PolicyDecisionCode.EVICTED_OLDEST,
        token_count=6,
        text="My name is Mara Venn.",
    )
    assert record.text
    assert record.status is MemoryStatus.EVICTED


def test_the_stimulus_record_keeps_the_phase_a_writer_never_saw():
    assert STIMULUS.phase == "orientation"
    assert STIMULUS.reliability == "reliable"
