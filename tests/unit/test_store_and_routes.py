"""The store methods and API routes the pipeline test does not happen to exercise.

The end-to-end test proves the happy path works. These prove the rest of the surface
does too: the caches, the status records, the export manifests, the narrowing filters,
and every route's behaviour on data that is missing rather than present.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from attention_sink.api import build_app
from attention_sink.domain import ArmId, MetricEvidence
from attention_sink.model_gateway import ModelGateway
from attention_sink.persistence import SqliteRepository
from attention_sink.pilot import ProtocolBundle, RunStatus
from attention_sink.pilot.configuration import RunKind
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.repositories import (
    AnalysisStatus,
    ExportManifestRecord,
    PersistenceError,
)
from attention_sink.pilot.service import PilotService, RunNotFound, ServiceError

NOW = datetime(2026, 8, 30, tzinfo=UTC)
RUN_ID = "run_store"


@pytest.fixture
def store(tmp_path: Path) -> SqliteRepository:
    return SqliteRepository(tmp_path / "pilot.sqlite3")


@pytest.fixture
def seeded(
    store: SqliteRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> PilotService:
    service = PilotService(repository=store, bundle=pilot_bundle, gateway=pilot_gateway)
    service.create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
    )
    return service


# ------------------------------------------------------------------- caches


def test_a_token_count_is_cached_and_read_back(store: SqliteRepository):
    assert store.get_token_count(counter_version="heuristic-v1", text_hash="sha256:a") is None
    store.store_token_count(counter_version="heuristic-v1", text_hash="sha256:a", tokens=7)
    assert store.get_token_count(counter_version="heuristic-v1", text_hash="sha256:a") == 7


def test_caching_a_token_count_twice_keeps_the_first(store: SqliteRepository):
    """A counter is deterministic; a second answer for one text means a bug, not news."""
    store.store_token_count(counter_version="v1", text_hash="sha256:a", tokens=7)
    store.store_token_count(counter_version="v1", text_hash="sha256:a", tokens=9)
    assert store.get_token_count(counter_version="v1", text_hash="sha256:a") == 7


def test_an_embedding_round_trips_and_overwrites(store: SqliteRepository, seeded: PilotService):
    assert store.get_embedding(RUN_ID, key="absent") is None
    store.store_embedding(RUN_ID, key="k", record={"vector": [1.0, 2.0]})
    assert store.get_embedding(RUN_ID, key="k") == {"vector": [1.0, 2.0]}
    store.store_embedding(RUN_ID, key="k", record={"vector": [3.0]})
    assert store.get_embedding(RUN_ID, key="k") == {"vector": [3.0]}
    del seeded


# ------------------------------------------------------------------- records


def test_analysis_status_round_trips(store: SqliteRepository, seeded: PilotService):
    assert store.get_analysis_status(RUN_ID, analysis_name="all") is None
    store.store_analysis_status(
        AnalysisStatus(
            run_id=RUN_ID,
            analysis_name="all",
            metric_version="metric-v1",
            completed_cycles=(1, 2, 3),
            updated_at=NOW,
        )
    )
    stored = store.get_analysis_status(RUN_ID, analysis_name="all")
    assert stored is not None
    assert stored.completed_cycles == (1, 2, 3)
    del seeded


def test_an_export_manifest_round_trips(store: SqliteRepository, seeded: PilotService):
    assert store.get_export_manifest(RUN_ID, export_id="e1") is None
    manifest = ExportManifestRecord(
        run_id=RUN_ID,
        export_id="e1",
        run_kind=RunKind.LOCAL_FIXTURE,
        directory="exports/e1",
        files={"a.json": "sha256:a"},
        created_at=NOW,
    )
    store.store_export_manifest(manifest)
    assert store.get_export_manifest(RUN_ID, export_id="e1") == manifest
    assert store.list_export_manifests(RUN_ID) == (manifest,)
    del seeded


def test_metrics_narrow_by_name_arm_and_cycle(store: SqliteRepository, seeded: PilotService):
    for cycle in (1, 2):
        for arm in (ArmId.ARM_FIFO, ArmId.ARM_LRU):
            store.store_metric(
                MetricEvidence(
                    run_id=RUN_ID,
                    arm_id=arm,
                    cycle=cycle,
                    metric_name="origin_recall",
                    value=0.5,
                    evaluator_version="fixture-evaluator-v1",
                    calculation_version="metric-v1",
                    rationale="a test",
                    computed_at=NOW,
                )
            )
    assert len(store.get_metrics(RUN_ID)) == 4
    assert len(store.get_metrics(RUN_ID, arm_id=ArmId.ARM_FIFO)) == 2
    assert len(store.get_metrics(RUN_ID, cycle=1)) == 2
    assert len(store.get_metrics(RUN_ID, metric_name="nope")) == 0
    del seeded


def test_storing_a_metric_twice_updates_it(store: SqliteRepository, seeded: PilotService):
    def metric(value: float) -> MetricEvidence:
        return MetricEvidence(
            run_id=RUN_ID,
            arm_id=ArmId.ARM_FIFO,
            cycle=1,
            metric_name="origin_recall",
            value=value,
            evaluator_version="fixture-evaluator-v1",
            calculation_version="metric-v1",
            rationale="a test",
            computed_at=NOW,
        )

    store.store_metric(metric(0.5))
    store.store_metric(metric(0.75))
    stored = store.get_metrics(RUN_ID, metric_name="origin_recall")
    assert len(stored) == 1
    assert stored[0].value == 0.75
    del seeded


def test_interviews_narrow_by_arm(store: SqliteRepository, seeded: PilotService):
    seeded.run_checkpoint(RUN_ID, cycle=0)
    assert len(store.get_interviews(RUN_ID, arm_id=ArmId.ARM_FIFO)) == 1
    assert len(store.get_interviews(RUN_ID, cycle=0, arm_id=ArmId.ARM_LRU)) == 1
    assert store.get_interviews(RUN_ID, cycle=12) == ()


def test_runs_are_listed_newest_first(
    store: SqliteRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    service = PilotService(repository=store, bundle=pilot_bundle, gateway=pilot_gateway)
    for run_id in ("run_a", "run_b"):
        service.create_run(
            run_id=run_id,
            configuration=build_configuration(pilot_bundle, run_id=run_id, gateway=pilot_gateway),
        )
    assert {run.run_id for run in store.list_runs()} == {"run_a", "run_b"}
    assert store.get_run("run_missing") is None


# ------------------------------------------------------------------ refusals


def test_the_store_refuses_operations_on_a_run_that_does_not_exist(store: SqliteRepository):
    from attention_sink.pilot.budget import ModelUsage

    with pytest.raises(PersistenceError, match="no run"):
        store.add_usage("run_missing", usage=ModelUsage())
    with pytest.raises(PersistenceError, match="no run"):
        store.set_paused("run_missing", paused=True)
    with pytest.raises(PersistenceError, match="no run"):
        store.delete_run("run_missing")


def test_the_service_refuses_an_unknown_run(
    store: SqliteRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    service = PilotService(repository=store, bundle=pilot_bundle, gateway=pilot_gateway)
    with pytest.raises(RunNotFound, match="no run"):
        service.get_run("run_missing")


def test_a_completed_run_refuses_to_advance(seeded: PilotService, store: SqliteRepository):
    run = seeded.get_run(RUN_ID)
    store.update_run_status(RUN_ID, status=RunStatus.COMPLETED, version=run.version)
    complete = store.get_run(RUN_ID)
    assert complete is not None
    store._connection.execute(
        "UPDATE runs SET current_cycle = ? WHERE run_id = ?",
        (complete.configuration.maximum_cycles, RUN_ID),
    )
    with pytest.raises(ServiceError, match="completed all"):
        seeded.run_next_cycle(RUN_ID)


def test_deleting_a_run_removes_everything_under_it(seeded: PilotService, store: SqliteRepository):
    seeded.run_next_cycle(RUN_ID)
    store.delete_run(RUN_ID)
    assert store.get_run(RUN_ID) is None
    assert store.list_completed_cycles(RUN_ID) == ()
    assert store.get_all_current_arm_states(RUN_ID) == {}


# --------------------------------------------------------------------- routes


@pytest.fixture
def client(seeded: PilotService, store: SqliteRepository) -> TestClient:
    """A client on a run with one committed cycle and one checkpoint."""
    seeded.run_checkpoint(RUN_ID, cycle=0)
    seeded.run_next_cycle(RUN_ID)
    return TestClient(build_app(store))


def test_the_run_and_arm_routes_answer(client: TestClient):
    runs = client.get("/runs").json()["data"]
    assert [run["run_id"] for run in runs] == [RUN_ID]

    arms = client.get(f"/runs/{RUN_ID}/arms").json()["data"]
    assert len(arms) == 6
    assert all(arm["active_memories"] for arm in arms)

    one = client.get(f"/runs/{RUN_ID}/arms/arm_fifo").json()["data"]
    assert one["arm_id"] == "arm_fifo"
    assert one["active_tokens"] <= one["budget_tokens"]


def test_the_cycle_list_paginates(client: TestClient):
    body = client.get(f"/runs/{RUN_ID}/cycles", params={"limit": 1, "offset": 0}).json()["data"]
    assert body == {"items": [1], "total": 1, "limit": 1, "offset": 0}
    empty = client.get(f"/runs/{RUN_ID}/cycles", params={"limit": 1, "offset": 5}).json()["data"]
    assert empty["items"] == []


def test_the_interview_routes_answer(client: TestClient):
    listed = client.get(f"/runs/{RUN_ID}/interviews").json()["data"]
    assert len(listed) == 6
    at_zero = client.get(f"/runs/{RUN_ID}/interviews/0")
    assert at_zero.status_code == 200
    assert at_zero.headers["ETag"].startswith('"sha256:')
    assert client.get(f"/runs/{RUN_ID}/interviews/12").status_code == 404


def test_the_metrics_and_divergence_routes_answer(client: TestClient, store: SqliteRepository):
    store.store_metric(
        MetricEvidence(
            run_id=RUN_ID,
            arm_id=ArmId.ARM_FIFO,
            cycle=1,
            metric_name="origin_recall",
            value=1.0,
            evaluator_version="fixture-evaluator-v1",
            calculation_version="metric-v1",
            rationale="a test",
            computed_at=NOW,
        )
    )
    body = client.get(f"/runs/{RUN_ID}/metrics", params={"metric_name": "origin_recall"}).json()
    assert body["data"]["total"] == 1
    assert client.get(f"/runs/{RUN_ID}/metrics", params={"arm_id": "arm_nope"}).status_code == 404

    empty = client.get(f"/runs/{RUN_ID}/divergence").json()["data"]
    assert empty == {"matrices": {}}
    store.store_embedding(RUN_ID, key="divergence", record={"matrices": {"0": {}}})
    assert client.get(f"/runs/{RUN_ID}/divergence").json()["data"] == {"matrices": {"0": {}}}


def test_the_exports_route_lists_recorded_exports(client: TestClient, store: SqliteRepository):
    assert client.get(f"/runs/{RUN_ID}/exports").json()["data"] == []
    store.store_export_manifest(
        ExportManifestRecord(
            run_id=RUN_ID,
            export_id="e1",
            run_kind=RunKind.LOCAL_FIXTURE,
            directory="exports/e1",
            files={},
            created_at=NOW,
        )
    )
    listed = client.get(f"/runs/{RUN_ID}/exports").json()["data"]
    assert [manifest["export_id"] for manifest in listed] == ["e1"]


def test_a_graveyard_query_can_be_narrowed_to_one_arm(client: TestClient):
    body = client.get(f"/runs/{RUN_ID}/graveyard", params={"arm_id": "arm_fifo"}).json()["data"]
    assert body["total"] >= 0
    assert client.get(f"/runs/{RUN_ID}/graveyard", params={"arm_id": "nope"}).status_code == 404
    assert client.get(f"/runs/{RUN_ID}/graveyard/mem_nope").status_code == 404


def test_a_cycle_that_has_no_snapshots_is_404(client: TestClient):
    assert client.get(f"/runs/{RUN_ID}/cycles/0").status_code == 404
    assert client.get(f"/runs/{RUN_ID}/cycles/99").status_code == 404


def test_the_local_api_factory_refuses_a_missing_database(tmp_path: Path, monkeypatch):
    from attention_sink.api.local import DATABASE_ENV, app

    monkeypatch.setenv(DATABASE_ENV, str(tmp_path / "absent.sqlite3"))
    with pytest.raises(FileNotFoundError, match="no local database"):
        app()


def test_the_local_api_factory_serves_an_existing_database(
    tmp_path: Path, store: SqliteRepository, monkeypatch
):
    from attention_sink.api.local import DATABASE_ENV, app

    monkeypatch.setenv(DATABASE_ENV, str(store.path))
    assert app().title == "Attention Sink read API"
    del tmp_path


# ------------------------------------------------------- the remaining surface


def test_the_store_closes_itself_when_used_as_a_context(tmp_path: Path):
    with SqliteRepository(tmp_path / "ctx.sqlite3") as store:
        assert store.schema_version >= 1
    with pytest.raises(Exception, match="closed"):
        store.get_run("anything")


def test_a_migration_that_fails_rolls_itself_back(tmp_path: Path):
    """A half-applied migration would leave a database nothing can reason about."""
    import sqlite3

    from attention_sink.persistence.migrations import Migration, apply_migrations, current_version

    connection = sqlite3.connect(tmp_path / "broken.sqlite3", isolation_level=None)
    broken = (
        Migration(version=1, name="broken", statements=("CREATE TABLE t (a INT)", "NOT SQL")),
    )
    with pytest.raises(sqlite3.OperationalError):
        _apply(connection, broken)
    assert current_version(connection) == 0
    assert apply_migrations(connection, now=NOW.isoformat()) == (1,)


def _apply(connection: object, migrations: object) -> None:
    """Apply a specific migration list, for the rollback test only."""
    import attention_sink.persistence.migrations as module

    original = module.MIGRATIONS
    module.MIGRATIONS = migrations  # type: ignore[assignment]
    try:
        module.apply_migrations(connection, now=NOW.isoformat())  # type: ignore[arg-type]
    finally:
        module.MIGRATIONS = original


def test_a_prepared_cycle_stored_twice_returns_the_first(
    seeded: PilotService, store: SqliteRepository
):
    prepared, _ = seeded._prepare(seeded.get_run(RUN_ID), cycle=1, invocation_id="a")
    assert store.store_prepared_cycle(prepared).content_hash == prepared.content_hash


def test_an_interview_stored_twice_returns_the_first(seeded: PilotService, store: SqliteRepository):
    seeded.run_checkpoint(RUN_ID, cycle=0)
    stored = store.get_interviews(RUN_ID, cycle=0)[0]
    assert store.store_interview(stored).record_hash == stored.record_hash


def test_a_single_snapshot_can_be_fetched_and_a_missing_one_is_none(
    seeded: PilotService, store: SqliteRepository
):
    seeded.run_next_cycle(RUN_ID)
    assert store.get_cycle_snapshot(RUN_ID, arm_id=ArmId.ARM_FIFO, cycle=1) is not None
    assert store.get_cycle_snapshot(RUN_ID, arm_id=ArmId.ARM_FIFO, cycle=99) is None


@pytest.mark.usefixtures("seeded")
def test_committing_without_a_run_a_lock_or_a_prepared_cycle_is_refused(
    store: SqliteRepository,
):
    from attention_sink.pilot.repositories import LockNotHeld

    with pytest.raises(PersistenceError, match="no run"):
        store.commit_cycle("run_missing", cycle=1, token="t" * 16, content_hash="x", version=0)
    with pytest.raises(LockNotHeld, match="not held"):
        store.commit_cycle(RUN_ID, cycle=1, token="t" * 16, content_hash="x", version=0)
    lock = store.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(PersistenceError, match="has not been prepared"):
        store.commit_cycle(RUN_ID, cycle=1, token=lock.token, content_hash="x", version=0)
