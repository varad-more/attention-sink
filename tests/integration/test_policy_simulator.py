"""The simulator runs the real mechanism, not a description of it."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from scripts.simulate_policy import decide, main

from attention_sink.domain import CANONICAL_ARMS, ArmId, PolicyDecision

FIXTURE = Path("datasets/fixtures/policy_simulator/divergence.json")


def load() -> dict[str, Any]:
    payload: dict[str, Any] = json.loads(FIXTURE.read_text(encoding="utf-8"))
    return payload


def test_the_shipped_fixture_runs_for_every_canonical_arm() -> None:
    fixture = load()
    for arm in CANONICAL_ARMS:
        decision, after = decide(arm, fixture)
        assert decision.is_final
        assert after.active_tokens <= fixture["budget"]["max_active_tokens"]
        assert PolicyDecision.model_validate(decision.model_dump(mode="json")) == decision


def test_the_arms_diverge_on_identical_input() -> None:
    fixture = load()
    outcomes = {
        arm: tuple(m.rsplit("_", 1)[-1] for m in decide(arm, fixture)[1].active_memory_ids)
        for arm in CANONICAL_ARMS
    }
    assert len(set(outcomes.values())) > 1, outcomes


def test_a_pinned_seed_survives_in_every_arm() -> None:
    fixture = load()
    for arm in CANONICAL_ARMS:
        _, after = decide(arm, fixture)
        assert f"mem_{arm.value}_000000" in after.active_memory_ids


def test_the_run_replays_identically(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURE)]) == 0
    first = capsys.readouterr().out
    assert main([str(FIXTURE)]) == 0
    assert capsys.readouterr().out == first


def test_the_output_is_labelled_simulated(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURE), "--arm", "arm_fifo"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["simulated"] is True
    assert set(payload["arms"]) == {"arm_fifo"}


def test_a_simulated_summary_is_labelled_as_such(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURE), "--arm", "arm_summary"]) == 0
    payload = json.loads(capsys.readouterr().out)
    created = payload["arms"]["arm_summary"]["decision"]["created_memories"]
    assert created
    assert all(m["text"].startswith("[simulated summary") for m in created)


def test_the_summary_line_reports_one_row_per_arm(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([str(FIXTURE), "--summary"]) == 0
    lines = [line for line in capsys.readouterr().out.splitlines() if line.strip()]
    assert len(lines) == len(CANONICAL_ARMS)


def test_an_unsatisfiable_budget_is_reported_and_exits_non_zero(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fixture = load()
    fixture["budget"]["max_active_tokens"] = 5
    path = tmp_path / "impossible.json"
    path.write_text(json.dumps(fixture), encoding="utf-8")

    assert main([str(path), "--arm", "arm_fifo"]) == 1
    payload = json.loads(capsys.readouterr().out)
    error = payload["arms"]["arm_fifo"]
    assert "over the budget" in error["error"]
    assert error["context"]["arm_id"] == ArmId.ARM_FIFO.value
