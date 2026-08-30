"""The whole local application, once: create, run, analyse, export, verify, serve.

One twenty-four cycle SQLite run shared by every test in the module, because building
it is the expensive part and every assertion below is about the same finished artefact.
This is the closest thing the repository has to "does Phase 5 work", so it is
deliberately end to end rather than mocked anywhere.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

import pytest
from fastapi.testclient import TestClient

from attention_sink.analysis import (
    EXPORT_FILES,
    AnalysisResult,
    AnalysisService,
    build_graveyard,
    export_dataset,
    export_labels,
    verify_checksums,
)
from attention_sink.api import build_app, registered_methods, route_paths
from attention_sink.domain import ArmId, MemoryStatus
from attention_sink.model_gateway import ModelGateway
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot import ModelSpec, ProtocolBundle, RunStatus
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.service import PilotService
from tests.conftest import fixed_clock

RUN_ID = "run_pipeline"
CYCLES = 24


class Pipeline(NamedTuple):
    """One finished local run, and everything derived from it."""

    repository: SqliteRepository
    service: PilotService
    analysis: AnalysisResult
    export: Path


@pytest.fixture(scope="module")
def pipeline(
    tmp_path_factory: pytest.TempPathFactory,
    pilot_bundle: ProtocolBundle,
) -> Pipeline:
    """Create a run, advance it twenty-four cycles, analyse it, and export it."""
    from attention_sink.model_gateway import GatewaySettings, build_gateway

    gateway = build_gateway(GatewaySettings.from_env(env={}))
    root = tmp_path_factory.mktemp("pipeline")
    repository = SqliteRepository(root / "pilot.sqlite3")
    service = PilotService(
        repository=repository, bundle=pilot_bundle, gateway=gateway, engine_clock=fixed_clock
    )
    service.create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=gateway),
    )
    service.run_checkpoint(RUN_ID, cycle=0)
    for _ in range(CYCLES):
        service.run_next_cycle(RUN_ID)

    analysis = AnalysisService(repository=repository, bundle=pilot_bundle, gateway=gateway)
    result = analysis.analyse_run(RUN_ID)
    directory = root / "dataset"
    export_dataset(
        directory,
        run=service.get_run(RUN_ID),
        repository=repository,
        bundle=pilot_bundle,
        analysis=result,
    )
    return Pipeline(repository, service, result, directory)


@pytest.fixture(scope="module")
def client(pipeline: Pipeline) -> TestClient:
    """A test client on the read API over the finished run."""
    return TestClient(build_app(pipeline.repository))


# ------------------------------------------------------------------- the run


def test_the_complete_run_persists_in_sqlite(pipeline: Pipeline):
    run = pipeline.service.get_run(RUN_ID)
    assert run.current_cycle == CYCLES
    assert run.status is RunStatus.COMPLETED
    assert pipeline.repository.list_completed_cycles(RUN_ID) == tuple(range(1, CYCLES + 1))
    assert len(pipeline.repository.list_all_snapshots(RUN_ID)) == 6 * CYCLES


def test_interviews_exist_at_every_checkpoint(pipeline: Pipeline):
    interviews = pipeline.repository.get_interviews(RUN_ID)
    assert {i.cycle for i in interviews} == {0, 12, 24}
    assert len(interviews) == 18
    assert all(i.record_hash and i.input_state_hash for i in interviews)


def test_every_arm_ends_within_the_budget(pipeline: Pipeline):
    run = pipeline.service.get_run(RUN_ID)
    budget = run.configuration.memory_budget_tokens
    for state in pipeline.repository.get_all_current_arm_states(RUN_ID).values():
        assert state.active_tokens <= budget


def test_the_run_records_every_model_call_it_made(pipeline: Pipeline):
    usage = pipeline.service.get_run(RUN_ID).usage
    assert usage.calls_by_role["writer"] == 6 * CYCLES
    assert usage.calls_by_role["interviewer"] == 18
    assert "evaluator" not in usage.calls_by_role
    assert len(usage.ledger) == usage.total_calls


# ------------------------------------------------------------------ metrics


def test_all_four_primary_metrics_are_stored_with_evidence(pipeline: Pipeline):
    stored = pipeline.repository.get_metrics(RUN_ID)
    names = {metric.metric_name for metric in stored}
    assert {"origin_recall", "identity_drift", "graveyard_echo", "contradiction_rate"} <= names
    for metric in stored:
        assert metric.rationale
        assert metric.calculation_version
        assert metric.evaluator_version


def test_identity_drift_is_zero_at_cycle_zero(pipeline: Pipeline):
    """An arm cannot have drifted from itself before a single cycle has run."""
    baseline = pipeline.repository.get_metrics(RUN_ID, metric_name="identity_drift", cycle=0)
    assert baseline
    assert all(metric.value == pytest.approx(0.0, abs=1e-9) for metric in baseline)


def test_the_divergence_matrix_is_symmetric_at_every_checkpoint(pipeline: Pipeline):
    assert set(pipeline.analysis.divergence) == {"0", "12", "24"}
    for matrix in pipeline.analysis.divergence.values():
        for left, row in matrix.items():
            assert row[left] == 0.0
            for right, value in row.items():
                assert value == pytest.approx(matrix[right][left])


def test_the_secondary_metrics_need_no_model_call(pipeline: Pipeline):
    stored = pipeline.repository.get_metrics(RUN_ID, metric_name="active_tokens")
    assert stored
    run = pipeline.service.get_run(RUN_ID)
    assert all(metric.value <= run.configuration.memory_budget_tokens for metric in stored)


# ---------------------------------------------------------------- graveyard


def test_the_graveyard_distinguishes_eviction_from_compression(pipeline: Pipeline):
    entries = pipeline.analysis.graveyard
    assert entries
    compressed = [e for e in entries if e.status is MemoryStatus.COMPRESSED]
    evicted = [e for e in entries if e.status is MemoryStatus.EVICTED]
    assert compressed and evicted
    assert all(e.summary_descendant_id is not None for e in compressed)
    assert all(not e.genuinely_inaccessible for e in compressed)
    assert all(e.genuinely_inaccessible for e in evicted)


def test_only_the_summarising_arm_has_compressed_entries(pipeline: Pipeline):
    compressed = {
        e.arm_id for e in pipeline.analysis.graveyard if e.status is MemoryStatus.COMPRESSED
    }
    assert compressed == {ArmId.ARM_SUMMARY}


def test_every_graveyard_entry_names_the_snapshot_that_retired_it(pipeline: Pipeline):
    hashes = {s.snapshot_hash for s in pipeline.repository.list_all_snapshots(RUN_ID)}
    for entry in pipeline.analysis.graveyard:
        assert entry.snapshot_evidence in hashes
        assert entry.lifespan == entry.retirement_cycle - entry.birth_cycle


def test_the_graveyard_is_derived_and_agrees_with_the_snapshots(pipeline: Pipeline):
    derived = build_graveyard(
        RUN_ID, pipeline.repository.list_arm_snapshots(RUN_ID, arm_id=ArmId.ARM_FIFO)
    )
    retired = sum(
        len(s.retired_memories)
        for s in pipeline.repository.list_arm_snapshots(RUN_ID, arm_id=ArmId.ARM_FIFO)
    )
    assert len(derived) == retired


def test_echo_measurements_use_the_documented_formula(pipeline: Pipeline):
    assert pipeline.analysis.echoes
    for echo in pipeline.analysis.echoes:
        assert echo.echo_delta == pytest.approx(echo.forgotten_similarity - echo.active_similarity)


# ---------------------------------------------------------------------- API


def test_the_api_registers_no_write_route(client: TestClient):
    """Read-only by construction: administrative actions stay on the command line."""
    assert registered_methods(client.app) == {"GET"}  # type: ignore[arg-type]


def test_every_documented_route_is_served(client: TestClient):
    served = set(route_paths(client.app))  # type: ignore[arg-type]
    expected = {
        "/health",
        "/version",
        "/runs",
        "/runs/{run_id}",
        "/runs/{run_id}/arms",
        "/runs/{run_id}/arms/{arm_id}",
        "/runs/{run_id}/cycles",
        "/runs/{run_id}/cycles/{cycle}",
        "/runs/{run_id}/graveyard",
        "/runs/{run_id}/graveyard/{memory_id}",
        "/runs/{run_id}/interviews",
        "/runs/{run_id}/interviews/{cycle}",
        "/runs/{run_id}/metrics",
        "/runs/{run_id}/divergence",
        "/runs/{run_id}/lineage/{memory_id}",
        "/runs/{run_id}/exports",
    }
    assert expected <= served


def test_the_api_refuses_a_cycle_that_has_not_been_committed(client: TestClient):
    assert client.get(f"/runs/{RUN_ID}/cycles/{CYCLES + 1}").status_code == 404


def test_the_api_never_exposes_a_prepared_cycle(pipeline: Pipeline, client: TestClient):
    """A prepared cycle describes a cycle that has not happened yet.

    It must not be reachable by any route, under any field name.
    """
    prepared = pipeline.repository.get_prepared_cycle(RUN_ID, cycle=CYCLES)
    assert prepared is not None
    body = json.dumps(client.get(f"/runs/{RUN_ID}/cycles/{CYCLES}").json())
    assert "prepared" not in body.lower()
    assert prepared.content_hash not in body


@pytest.mark.usefixtures("pipeline")
def test_a_cycle_response_carries_only_its_own_stimulus(client: TestClient):
    """The deck is never consulted, so a future stimulus cannot be served."""
    body = client.get(f"/runs/{RUN_ID}/cycles/3").json()
    served = {view["stimulus_id"] for view in body["data"]}
    assert served == {"stim_003"}
    text = json.dumps(body)
    assert "evaluator_notes" not in text
    assert "pressure_type" not in text
    assert "reliability" not in text


def test_a_cycle_response_publishes_prompt_versions_but_no_prompt_text(client: TestClient):
    view = client.get(f"/runs/{RUN_ID}/cycles/1").json()["data"][0]
    assert view["prompt_versions"]
    assert view["prompt_hashes"]
    assert "prompt_text" not in view
    assert "system" not in view


def test_an_immutable_record_carries_an_etag_and_a_long_cache(client: TestClient):
    response = client.get(f"/runs/{RUN_ID}/cycles/1")
    assert response.headers["ETag"].startswith('"sha256:')
    assert "immutable" in response.headers["Cache-Control"]


def test_a_moving_view_is_not_cached(client: TestClient):
    assert client.get(f"/runs/{RUN_ID}").headers["Cache-Control"] == "no-cache"


def test_every_response_says_what_the_run_it_describes_is(client: TestClient, pipeline: Pipeline):
    """Derived from the run, never defaulted.

    The first deployed API told every reader that a run driven by real Bedrock
    generations was a local fixture, because the envelope's `simulated` and `labels`
    were constants. A client renders responses, so the response is where this has to
    be right.
    """
    body = client.get(f"/runs/{RUN_ID}").json()
    run = pipeline.service.get_run(RUN_ID)
    assert body["simulated"] is run.configuration.simulated
    assert set(body["labels"]) == set(export_labels(run))


def test_the_graveyard_and_lineage_routes_answer(client: TestClient, pipeline: Pipeline):
    listed = client.get(f"/runs/{RUN_ID}/graveyard", params={"limit": 5}).json()["data"]
    assert listed["total"] > 0
    assert len(listed["items"]) == 5

    compressed = next(e for e in pipeline.analysis.graveyard if e.summary_descendant_id is not None)
    entry = client.get(f"/runs/{RUN_ID}/graveyard/{compressed.memory_id}").json()["data"]
    assert entry["genuinely_inaccessible"] is False
    lineage = client.get(f"/runs/{RUN_ID}/lineage/{compressed.memory_id}").json()["data"]
    assert compressed.summary_descendant_id in lineage["children"]


def test_unknown_runs_arms_and_memories_are_404(client: TestClient):
    assert client.get("/runs/nope").status_code == 404
    assert client.get(f"/runs/{RUN_ID}/arms/arm_nope").status_code == 404
    assert client.get(f"/runs/{RUN_ID}/lineage/mem_nope").status_code == 404


def test_health_and_version_answer_without_a_run(client: TestClient):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/version").json()["mode"] == "local_fixture"


# -------------------------------------------------------------------- export


def test_the_export_writes_every_documented_file(pipeline: Pipeline):
    written = {path.name for path in pipeline.export.iterdir()}
    assert set(EXPORT_FILES) <= written
    assert "checksums.sha256" in written


def test_every_exported_file_matches_its_checksum(pipeline: Pipeline):
    assert verify_checksums(pipeline.export) == ()


def test_a_corrupted_export_is_detected(pipeline: Pipeline, tmp_path: Path):
    import shutil

    copy = tmp_path / "corrupted"
    shutil.copytree(pipeline.export, copy)
    target = copy / "metrics.csv"
    target.write_text(target.read_text() + "tampered\n", encoding="utf-8")
    assert verify_checksums(copy) == ("metrics.csv",)


def test_the_export_is_labelled_local_fixture_and_non_canonical(pipeline: Pipeline):
    manifest = json.loads((pipeline.export / "run-manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["labels"]) == set(export_labels(pipeline.service.get_run(RUN_ID)))
    assert manifest["run_kind"] == "local_fixture"
    export_manifest = json.loads(
        (pipeline.export / "export-manifest.json").read_text(encoding="utf-8")
    )
    assert set(export_manifest["manifest"]["labels"]) == set(
        export_labels(pipeline.service.get_run(RUN_ID))
    )


def test_the_export_contains_one_line_per_snapshot_and_interview(pipeline: Pipeline):
    from attention_sink.analysis import read_jsonl

    assert len(read_jsonl(pipeline.export / "cycle-snapshots.jsonl")) == 6 * CYCLES
    assert len(read_jsonl(pipeline.export / "interviews.jsonl")) == 18
    assert len(read_jsonl(pipeline.export / "graveyard.jsonl")) == len(pipeline.analysis.graveyard)


def test_the_exported_model_usage_accounts_for_every_call(pipeline: Pipeline):
    rows = (pipeline.export / "model-usage.csv").read_text(encoding="utf-8").splitlines()
    assert len(rows) - 1 == pipeline.service.get_run(RUN_ID).usage.total_calls


def test_the_export_is_recorded_against_the_run(pipeline: Pipeline):
    manifests = pipeline.repository.list_export_manifests(RUN_ID)
    assert len(manifests) == 1
    assert manifests[0].files
    assert set(manifests[0].labels) == set(export_labels(pipeline.service.get_run(RUN_ID)))


def test_an_export_is_labelled_from_the_run_and_not_from_a_constant(
    tmp_path: Path, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """The defect the first deployed export made.

    `EXPORT_LABELS` is what a local fixture run carries, and it was stamped on every
    export unconditionally -- so the first dataset produced by real Bedrock
    generations arrived labelled `LOCAL_FIXTURE / SIMULATED_MODEL_OUTPUTS`. An export
    is the artefact most likely to be read by somebody who was not here, and a wrong
    provenance label on one is a false result rather than a cosmetic slip.
    """
    from attention_sink.analysis import export_labels
    from attention_sink.pilot.configuration import RunKind

    repository = SqliteRepository(tmp_path / "labels.sqlite3")
    service = PilotService(repository=repository, bundle=pilot_bundle, gateway=pilot_gateway)
    configuration = build_configuration(pilot_bundle, run_id="run_labels", gateway=pilot_gateway)
    service.create_run(run_id="run_labels", configuration=configuration)
    local = service.get_run("run_labels")

    assert export_labels(local) == (
        "LOCAL_FIXTURE",
        "NON_CANONICAL",
        "SIMULATED_MODEL_OUTPUTS",
        "APPROXIMATE_TOKEN_BUDGET",
    )

    real = ModelSpec(
        model_id="amazon.nova-micro-v1:0",
        region="us-east-1",
        temperature=0.7,
        top_p=0.9,
        max_output_tokens=1024,
        simulated=False,
    )
    staging = local.model_copy(
        update={
            "run_kind": RunKind.AWS_STAGING,
            "configuration": configuration.model_copy(
                update={
                    "run_kind": RunKind.AWS_STAGING,
                    "writer_model": real,
                    "embedding_model": real,
                    "token_count_source": "bedrock_count_tokens",
                }
            ),
        }
    )
    assert export_labels(staging) == (
        "AWS_STAGING",
        "NON_CANONICAL",
        "REAL_MODEL_OUTPUTS",
        "EXACT_TOKEN_BUDGET",
    )
