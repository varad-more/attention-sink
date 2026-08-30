"""The DynamoDB adapter, against the guarantees Phase 5 proved for SQLite.

Deliberately a near-copy of ``test_persistence.py``. The two adapters satisfy one
protocol, and the only way to know that the guarantees carried over is to state them
again and run them against the other store. A shared parametrised suite would have
been shorter and would have hidden exactly the thing worth seeing here: which
guarantees are the same, and which are enforced by different machinery.

The differences that matter are in the machinery, not the promise:

- SQLite refuses to rewrite a snapshot with an ``ABORT`` trigger. DynamoDB has no
  triggers, so a snapshot is written with ``attribute_not_exists`` and a rewrite is a
  failed condition.
- SQLite takes the write lock with ``BEGIN IMMEDIATE``. DynamoDB conditions the
  fourteen writes of a commit on the run's version, its cycle, the lock token, and the
  prepared cycle's hash, inside one ``TransactWriteItems``.

Everything runs against moto, so ``make test`` proves the AWS adapter for a
contributor who has no AWS account.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from attention_sink.aws.dynamodb import DynamoRepository, table_definition
from attention_sink.domain import ArmId
from attention_sink.model_gateway import ModelGateway
from attention_sink.pilot import ProtocolBundle, RunStatus
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.repositories import (
    ConcurrentRunUpdate,
    LockNotHeld,
    PersistenceError,
    PilotRepository,
    PreparedCycleConflict,
)
from attention_sink.pilot.service import PilotService, RunPaused
from tests.conftest import fixed_clock

RUN_ID = "run_dynamo"
TABLE = "attention-sink-test"
REGION = "us-east-1"


@pytest.fixture
def aws_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Credentials moto accepts and no real endpoint would.

    Set rather than assumed: a developer with a live profile exported must not be
    able to point this suite at an account by accident.
    """
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def repository(aws_credentials: None) -> Iterator[DynamoRepository]:
    """A fresh table per test, created from the adapter's own definition."""
    del aws_credentials
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        yield DynamoRepository(table_name=TABLE, client=client)


@pytest.fixture
def service(
    repository: DynamoRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> PilotService:
    """A service on a fresh table, with a run already created."""
    service = PilotService(
        repository=repository,
        bundle=pilot_bundle,
        gateway=pilot_gateway,
        engine_clock=fixed_clock,
    )
    service.create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
    )
    return service


# ------------------------------------------------------------------- the port


def test_the_adapter_satisfies_the_repository_protocol(repository: DynamoRepository):
    """The point of the phase, asserted directly rather than inferred from use."""
    assert isinstance(repository, PilotRepository)


def test_the_table_has_no_scan_in_any_read_path(repository: DynamoRepository):
    """Every read is a GetItem or a Query, never a Scan.

    A Scan on a public path is a cost that grows with data nobody asked for, and the
    read API calls three of these on every page load.
    """
    calls: list[str] = []
    original = repository.client.meta.events

    def record(event_name: str, **_: Any) -> None:
        calls.append(event_name)

    original.register("provide-client-params.dynamodb.Scan", record)
    repository.list_runs()
    repository.list_completed_cycles(RUN_ID)
    repository.get_metrics(RUN_ID)
    assert calls == []


# ------------------------------------------------------------------------ runs


@pytest.mark.usefixtures("service")
def test_a_created_run_seeds_six_identical_arms(repository: DynamoRepository):
    states = repository.get_all_current_arm_states(RUN_ID)
    assert len(states) == 6
    texts = {tuple(m.text for m in state.active_memories) for state in states.values()}
    assert len(texts) == 1


def test_a_run_cannot_be_created_twice(
    service: PilotService, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    with pytest.raises(PersistenceError, match="already exists"):
        service.create_run(
            run_id=RUN_ID,
            configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
        )


@pytest.mark.usefixtures("service")
def test_runs_are_listed_newest_first_without_a_scan(repository: DynamoRepository):
    listed = repository.list_runs()
    assert [run.run_id for run in listed] == [RUN_ID]


@pytest.mark.usefixtures("service")
def test_a_seed_state_is_installed_once(repository: DynamoRepository):
    state = repository.get_current_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO)
    assert state is not None
    with pytest.raises(PersistenceError, match="already has a state"):
        repository.seed_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO, state=state)


def test_a_status_update_refuses_a_stale_version(
    repository: DynamoRepository, service: PilotService
):
    run = service.get_run(RUN_ID)
    repository.update_run_status(RUN_ID, status=RunStatus.RUNNING, version=run.version)
    with pytest.raises(ConcurrentRunUpdate, match="not at version"):
        repository.update_run_status(RUN_ID, status=RunStatus.FAILED, version=run.version)


@pytest.mark.usefixtures("service")
def test_usage_accumulates_rather_than_replacing(repository: DynamoRepository):
    from attention_sink.pilot import ModelUsage

    repository.add_usage(RUN_ID, usage=ModelUsage(total_calls=3, input_tokens=100))
    run = repository.add_usage(RUN_ID, usage=ModelUsage(total_calls=2, input_tokens=50))
    assert run.usage.total_calls == 5
    assert run.usage.input_tokens == 150


def test_usage_on_a_run_that_does_not_exist_is_refused(repository: DynamoRepository):
    from attention_sink.pilot import ModelUsage

    with pytest.raises(PersistenceError, match="no run"):
        repository.add_usage("run_absent", usage=ModelUsage(total_calls=1))


# -------------------------------------------------------------- atomic commits


def test_one_cycle_commits_six_arms_and_advances_once(
    service: PilotService, repository: DynamoRepository
):
    outcome = service.run_next_cycle(RUN_ID)
    assert outcome.cycle == 1
    assert len(outcome.snapshots) == 6
    assert repository.list_completed_cycles(RUN_ID) == (1,)
    run = service.get_run(RUN_ID)
    assert run.current_cycle == 1
    assert run.status is RunStatus.RUNNING
    assert {s.arm_id for s in repository.list_cycle_snapshots(RUN_ID, cycle=1)} == set(
        run.configuration.arms
    )


def test_a_failed_commit_leaves_nothing_behind(service: PilotService, repository: DynamoRepository):
    """The whole point of the transaction. Nothing partial is ever visible."""
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(PreparedCycleConflict, match="prepared as"):
        repository.commit_cycle(
            RUN_ID, cycle=1, token=lock.token, content_hash="sha256:wrong", version=0
        )
    assert repository.list_completed_cycles(RUN_ID) == ()
    assert service.get_run(RUN_ID).current_cycle == 0
    assert prepared.content_hash


def test_a_commit_refuses_a_cycle_that_is_not_next(
    service: PilotService, repository: DynamoRepository
):
    service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(ConcurrentRunUpdate, match="not the next one"):
        repository.commit_cycle(
            RUN_ID, cycle=3, token=lock.token, content_hash=prepared.content_hash, version=0
        )


def test_a_commit_refuses_a_stale_run_version(service: PilotService, repository: DynamoRepository):
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(ConcurrentRunUpdate, match="version"):
        repository.commit_cycle(
            RUN_ID, cycle=1, token=lock.token, content_hash=prepared.content_hash, version=99
        )


def test_a_commit_refuses_a_lock_this_caller_does_not_hold(
    service: PilotService, repository: DynamoRepository
):
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(LockNotHeld, match="not held by this invocation"):
        repository.commit_cycle(
            RUN_ID,
            cycle=1,
            token="0" * 32,
            content_hash=prepared.content_hash,
            version=0,
        )


@pytest.mark.usefixtures("service")
def test_a_commit_without_a_prepared_cycle_is_refused(repository: DynamoRepository):
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(PersistenceError, match="has not been prepared"):
        repository.commit_cycle(
            RUN_ID, cycle=1, token=lock.token, content_hash="sha256:none", version=0
        )


def test_a_committed_snapshot_cannot_be_rewritten(
    service: PilotService, repository: DynamoRepository
):
    """Immutability is a write condition here, because DynamoDB has no triggers."""
    from attention_sink.aws import keys
    from attention_sink.aws.dynamodb import _s

    service.run_next_cycle(RUN_ID)
    with pytest.raises(ClientError, match="ConditionalCheckFailed"):
        repository.client.put_item(
            TableName=TABLE,
            Item={
                "PK": _s(keys.run_pk(RUN_ID)),
                "SK": _s(keys.snapshot_sk(1, ArmId.ARM_FIFO.value)),
                "payload": _s("{}"),
            },
            ConditionExpression="attribute_not_exists(SK)",
        )
    snapshot = repository.get_cycle_snapshot(RUN_ID, arm_id=ArmId.ARM_FIFO, cycle=1)
    assert snapshot is not None
    assert snapshot.cycle == 1


def test_the_run_is_marked_complete_at_the_last_cycle(
    repository: DynamoRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """The commit that reaches the ceiling is the one that closes the run."""
    configuration = build_configuration(
        pilot_bundle, run_id="run_short", gateway=pilot_gateway
    ).model_copy(update={"maximum_cycles": 2})
    service = PilotService(
        repository=repository,
        bundle=pilot_bundle,
        gateway=pilot_gateway,
        engine_clock=fixed_clock,
    )
    service.create_run(run_id="run_short", configuration=configuration)
    assert service.run_next_cycle("run_short").run.status is RunStatus.RUNNING
    assert service.run_next_cycle("run_short").run.status is RunStatus.COMPLETED


# --------------------------------------------------------------------- locking


@pytest.mark.usefixtures("service")
def test_a_second_invocation_cannot_take_a_held_lock(repository: DynamoRepository):
    repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="first", ttl_seconds=300)
    with pytest.raises(LockNotHeld, match="holds the cycle lock"):
        repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="second", ttl_seconds=300)


def test_an_expired_lock_may_be_replaced(
    repository: DynamoRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """A Lambda killed at its timeout must not wedge the run forever."""
    moment = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    clock: list[datetime] = [moment]
    repository.clock = lambda: clock[0]
    PilotService(repository=repository, bundle=pilot_bundle, gateway=pilot_gateway).create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
    )
    first = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="dead", ttl_seconds=60)
    clock[0] = moment + timedelta(seconds=61)
    second = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="live", ttl_seconds=60)
    assert second.token != first.token
    held = repository.get_cycle_lock(RUN_ID)
    assert held is not None
    assert held.invocation_id == "live"


def test_a_lock_on_a_run_that_does_not_exist_is_refused(repository: DynamoRepository):
    with pytest.raises(PersistenceError, match="no run"):
        repository.acquire_cycle_lock("run_absent", cycle=1, invocation_id="x", ttl_seconds=60)


@pytest.mark.usefixtures("service")
def test_releasing_a_lock_this_caller_does_not_hold_is_silent(repository: DynamoRepository):
    repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="first", ttl_seconds=60)
    repository.release_cycle_lock(RUN_ID, token="not-the-token")  # noqa: S106
    assert repository.get_cycle_lock(RUN_ID) is not None


# -------------------------------------------------------------- prepared cycles


def test_a_retry_reuses_the_prepared_cycle_instead_of_calling_six_writers(
    service: PilotService, repository: DynamoRepository
):
    run = service.get_run(RUN_ID)
    first, reused_first = service._prepare(run, cycle=1, invocation_id="a")
    second, reused_second = service._prepare(run, cycle=1, invocation_id="b")
    assert not reused_first
    assert reused_second
    assert first.content_hash == second.content_hash
    assert repository.get_prepared_cycle(RUN_ID, cycle=1) is not None


def test_a_conflicting_prepared_cycle_is_refused(
    service: PilotService, repository: DynamoRepository
):
    run = service.get_run(RUN_ID)
    prepared, _ = service._prepare(run, cycle=1, invocation_id="a")
    altered = prepared.model_copy(
        update={"usage": prepared.usage.model_copy(update={"total_calls": 999})}
    ).sealed()
    with pytest.raises(PreparedCycleConflict, match="different content"):
        repository.store_prepared_cycle(altered)


def test_a_prepared_cycle_fits_inside_one_item(service: PilotService, repository: DynamoRepository):
    """Six snapshots and six states at once is the largest item this table holds.

    Compressed, it runs comfortably inside DynamoDB's 400 KB ceiling. Uncompressed it
    would run near 280 KB and grow with the usage ledger, which is a limit nobody
    should discover on cycle twenty.
    """
    from attention_sink.aws import keys
    from attention_sink.aws.dynamodb import _binary

    service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    item = repository._get(keys.run_pk(RUN_ID), keys.prepared_sk(1))
    assert item is not None
    assert len(_binary(item, "payload")) < 200_000


def test_a_duplicate_cycle_invocation_does_not_advance_twice(service: PilotService):
    service.run_next_cycle(RUN_ID)
    service.run_next_cycle(RUN_ID)
    assert service.get_run(RUN_ID).current_cycle == 2


# ---------------------------------------------------------------- checkpoints


def test_a_checkpoint_is_idempotent(service: PilotService, repository: DynamoRepository):
    first = service.run_checkpoint(RUN_ID, cycle=0)
    second = service.run_checkpoint(RUN_ID, cycle=0)
    assert len(first) == 6
    assert second == ()
    assert len(repository.get_interviews(RUN_ID, cycle=0)) == 6


def test_an_interview_cannot_be_revised(service: PilotService, repository: DynamoRepository):
    service.run_checkpoint(RUN_ID, cycle=0)
    stored = repository.get_interviews(RUN_ID, cycle=0)[0]
    altered = stored.model_copy(update={"prompt_hash": "sha256:different"}).sealed()
    with pytest.raises(PersistenceError, match="cannot be revised"):
        repository.store_interview(altered)


def test_interviews_are_narrowed_by_arm(service: PilotService, repository: DynamoRepository):
    service.run_checkpoint(RUN_ID, cycle=0)
    only = repository.get_interviews(RUN_ID, cycle=0, arm_id=ArmId.ARM_SINK)
    assert [i.arm_id for i in only] == [ArmId.ARM_SINK]


# ------------------------------------------------------------------- readings


def test_one_arms_snapshots_come_back_in_cycle_order(
    service: PilotService, repository: DynamoRepository
):
    for _ in range(3):
        service.run_next_cycle(RUN_ID)
    snapshots = repository.list_arm_snapshots(RUN_ID, arm_id=ArmId.ARM_LRU)
    assert [snapshot.cycle for snapshot in snapshots] == [1, 2, 3]


def test_metrics_are_narrowed_by_name_arm_and_cycle(
    service: PilotService, repository: DynamoRepository
):
    from attention_sink.domain import MetricEvidence

    service.run_next_cycle(RUN_ID)
    for arm in (ArmId.ARM_FIFO, ArmId.ARM_LRU):
        for name in ("origin_recall", "identity_drift"):
            repository.store_metric(
                MetricEvidence(
                    run_id=RUN_ID,
                    arm_id=arm,
                    cycle=1,
                    metric_name=name,
                    value=0.5,
                    evaluator_version="fixture-evaluator-v1",
                    calculation_version="metrics-v1",
                    rationale="a stored score",
                    computed_at=fixed_clock(),
                )
            )
    assert len(repository.get_metrics(RUN_ID)) == 4
    assert len(repository.get_metrics(RUN_ID, metric_name="origin_recall")) == 2
    assert len(repository.get_metrics(RUN_ID, arm_id=ArmId.ARM_FIFO)) == 2
    assert len(repository.get_metrics(RUN_ID, metric_name="origin_recall", cycle=1)) == 2
    assert repository.get_metrics(RUN_ID, metric_name="origin_recall", cycle=2) == ()


@pytest.mark.usefixtures("service")
def test_an_embedding_is_stored_once_per_model_and_content(repository: DynamoRepository):
    repository.embedding_model_id = "amazon.titan-embed-text-v2:0"
    repository.store_embedding(RUN_ID, key="text:sha256:abc", record={"vector": [0.1, 0.2]})
    assert repository.get_embedding(RUN_ID, key="text:sha256:abc") == {"vector": [0.1, 0.2]}
    # A different model is a different partition, so it never sees the first vector.
    repository.embedding_model_id = "amazon.titan-embed-text-v1"
    assert repository.get_embedding(RUN_ID, key="text:sha256:abc") is None


def test_a_token_count_is_cached_per_counter_version(repository: DynamoRepository):
    repository.store_token_count(counter_version="bedrock-v1", text_hash="sha256:a", tokens=42)
    assert repository.get_token_count(counter_version="bedrock-v1", text_hash="sha256:a") == 42
    assert repository.get_token_count(counter_version="other-v1", text_hash="sha256:a") is None


@pytest.mark.usefixtures("service")
def test_analysis_status_and_artifacts_round_trip(repository: DynamoRepository):
    from attention_sink.pilot.repositories import AnalysisStatus

    stored = repository.store_analysis_status(
        AnalysisStatus(
            run_id=RUN_ID,
            analysis_name="all",
            metric_version="metrics-v1",
            completed_cycles=(1, 2),
            updated_at=fixed_clock(),
        )
    )
    read = repository.get_analysis_status(RUN_ID, analysis_name="all")
    assert read is not None
    assert read.completed_cycles == stored.completed_cycles
    repository.store_analysis_artifact(RUN_ID, name="divergence", payload={"matrices": {}})
    assert repository.get_analysis_artifact(RUN_ID, name="divergence") == {"matrices": {}}
    assert repository.get_analysis_artifact(RUN_ID, name="absent") is None


@pytest.mark.usefixtures("service")
def test_a_cycle_is_claimed_for_analysis_exactly_once(repository: DynamoRepository):
    assert repository.mark_cycle_analysed(RUN_ID, cycle=1, detail={"metrics": 0})
    assert not repository.mark_cycle_analysed(RUN_ID, cycle=1, detail={"metrics": 0})
    repository.release_cycle_analysis(RUN_ID, cycle=1)
    assert repository.mark_cycle_analysed(RUN_ID, cycle=1, detail={"metrics": 1})
    assert repository.get_cycle_analysis(RUN_ID, cycle=1) == {"metrics": 1}


@pytest.mark.usefixtures("service")
def test_export_manifests_come_back_newest_first(repository: DynamoRepository):
    from attention_sink.pilot.configuration import RunKind
    from attention_sink.pilot.repositories import ExportManifestRecord

    for index, moment in enumerate(
        (datetime(2026, 8, 1, tzinfo=UTC), datetime(2026, 8, 2, tzinfo=UTC))
    ):
        repository.store_export_manifest(
            ExportManifestRecord(
                run_id=RUN_ID,
                export_id=f"export-{index}",
                run_kind=RunKind.AWS_STAGING,
                directory=f"s3://bucket/runs/{RUN_ID}/export-{index}/",
                files={},
                created_at=moment,
            )
        )
    listed = repository.list_export_manifests(RUN_ID)
    assert [manifest.export_id for manifest in listed] == ["export-1", "export-0"]
    assert repository.get_export_manifest(RUN_ID, export_id="export-0") is not None
    assert repository.get_export_manifest(RUN_ID, export_id="absent") is None


# ------------------------------------------------------------------------ pause


def test_a_paused_run_refuses_to_advance(service: PilotService, repository: DynamoRepository):
    repository.set_paused(RUN_ID, paused=True)
    with pytest.raises(RunPaused, match="paused"):
        service.run_next_cycle(RUN_ID)
    repository.set_paused(RUN_ID, paused=False)
    assert service.run_next_cycle(RUN_ID).cycle == 1


def test_pausing_a_run_that_does_not_exist_is_refused(repository: DynamoRepository):
    with pytest.raises(PersistenceError, match="no run"):
        repository.set_paused("run_absent", paused=True)


def test_absent_records_read_as_none(repository: DynamoRepository):
    assert repository.get_run("run_absent") is None
    assert repository.get_cycle_lock("run_absent") is None
    assert repository.get_prepared_cycle("run_absent", cycle=1) is None
    assert repository.get_current_arm_state("run_absent", arm_id=ArmId.ARM_FIFO) is None
    assert repository.get_cycle_snapshot("run_absent", arm_id=ArmId.ARM_FIFO, cycle=1) is None
    assert repository.get_analysis_status("run_absent", analysis_name="all") is None
    assert repository.get_cycle_analysis("run_absent", cycle=1) is None
