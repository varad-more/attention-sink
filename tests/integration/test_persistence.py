"""The SQLite adapter, the atomic commit, and everything it refuses.

Organised by the guarantee under test rather than by method, because the guarantees
are what a second adapter in Phase 7 will have to reproduce. A test here that passed
only against SQLite would be worth nothing then.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from attention_sink.domain import ArmId
from attention_sink.model_gateway import ModelGateway
from attention_sink.persistence import MIGRATIONS, SqliteRepository
from attention_sink.pilot import ProtocolBundle, RunKind, RunStatus
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.repositories import (
    ConcurrentRunUpdate,
    LockNotHeld,
    PersistenceError,
    PreparedCycleConflict,
)
from attention_sink.pilot.service import PilotService, RunPaused, ServiceError
from tests.conftest import fixed_clock

RUN_ID = "run_persist"


@pytest.fixture
def repository(tmp_path: Path) -> SqliteRepository:
    """A fresh migrated database per test."""
    return SqliteRepository(tmp_path / "pilot.sqlite3")


@pytest.fixture
def service(
    repository: SqliteRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> PilotService:
    """A service on a fresh database, with a run already created."""
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


# ------------------------------------------------------------------ migrations


def test_migrations_are_applied_once_and_are_idempotent(tmp_path: Path):
    path = tmp_path / "pilot.sqlite3"
    first = SqliteRepository(path)
    assert first.schema_version == MIGRATIONS[-1].version
    assert first.migrate() == ()
    first.close()
    assert SqliteRepository(path).schema_version == MIGRATIONS[-1].version


def test_every_migration_version_is_unique_and_ascending():
    versions = [migration.version for migration in MIGRATIONS]
    assert versions == sorted(set(versions))


# ------------------------------------------------------------------------ runs


@pytest.mark.usefixtures("service")
def test_a_created_run_seeds_six_identical_arms(repository: SqliteRepository):
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
def test_a_seed_state_is_installed_once(repository: SqliteRepository):
    state = repository.get_current_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO)
    assert state is not None
    with pytest.raises(PersistenceError, match="already has a state"):
        repository.seed_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO, state=state)


def test_a_status_update_refuses_a_stale_version(
    repository: SqliteRepository, service: PilotService
):
    run = service.get_run(RUN_ID)
    repository.update_run_status(RUN_ID, status=RunStatus.RUNNING, version=run.version)
    with pytest.raises(ConcurrentRunUpdate, match="not at version"):
        repository.update_run_status(RUN_ID, status=RunStatus.FAILED, version=run.version)


# ------------------------------------------------------------- atomic commits


def test_one_cycle_commits_six_arms_and_advances_once(
    service: PilotService, repository: SqliteRepository
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


def test_a_failed_commit_leaves_nothing_behind(service: PilotService, repository: SqliteRepository):
    """The whole point of the transaction. A rollback must leave zero rows."""
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
    service: PilotService, repository: SqliteRepository
):
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(ConcurrentRunUpdate, match="not the next one"):
        repository.commit_cycle(
            RUN_ID, cycle=3, token=lock.token, content_hash=prepared.content_hash, version=0
        )


def test_a_commit_refuses_a_stale_run_version(service: PilotService, repository: SqliteRepository):
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=1, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(ConcurrentRunUpdate, match="version"):
        repository.commit_cycle(
            RUN_ID, cycle=1, token=lock.token, content_hash=prepared.content_hash, version=99
        )


def test_a_completed_snapshot_cannot_be_modified(
    service: PilotService, repository: SqliteRepository
):
    """Immutability is a database trigger, so it survives code nobody has written."""
    service.run_next_cycle(RUN_ID)
    connection = repository._connection
    with pytest.raises(sqlite3.IntegrityError, match="cannot be modified"):
        connection.execute("UPDATE cycle_snapshots SET payload = '{}' WHERE run_id = ?", (RUN_ID,))
    with pytest.raises(sqlite3.IntegrityError, match="cannot be deleted"):
        connection.execute("DELETE FROM cycle_snapshots WHERE run_id = ?", (RUN_ID,))


# ------------------------------------------------------------------- locking


@pytest.mark.usefixtures("service")
def test_a_second_invocation_cannot_take_a_held_lock(repository: SqliteRepository):
    repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="first", ttl_seconds=300)
    with pytest.raises(LockNotHeld, match="holds the cycle lock"):
        repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="second", ttl_seconds=300)


def test_an_expired_lock_may_be_replaced(
    tmp_path: Path, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """A scheduler killed mid-cycle must not wedge the run forever."""
    moment = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    clock: list[datetime] = [moment]
    repository = SqliteRepository(tmp_path / "p.sqlite3", clock=lambda: clock[0])
    PilotService(repository=repository, bundle=pilot_bundle, gateway=pilot_gateway).create_run(
        run_id=RUN_ID,
        configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=pilot_gateway),
    )
    first = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="dead", ttl_seconds=60)
    clock[0] = moment + timedelta(seconds=61)
    second = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="live", ttl_seconds=60)
    assert second.token != first.token
    assert repository.get_cycle_lock(RUN_ID) is not None


@pytest.mark.usefixtures("service")
def test_releasing_a_lock_this_caller_does_not_hold_is_silent(repository: SqliteRepository):
    repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="first", ttl_seconds=60)
    # A lock token, not a credential: S106 reads the parameter name, not what it holds.
    repository.release_cycle_lock(RUN_ID, token="not-the-token")  # noqa: S106
    assert repository.get_cycle_lock(RUN_ID) is not None


# ------------------------------------------------------- prepared cycles


def test_a_retry_reuses_the_prepared_cycle_instead_of_calling_six_writers(
    service: PilotService, repository: SqliteRepository
):
    run = service.get_run(RUN_ID)
    first, reused_first = service._prepare(run, cycle=1, invocation_id="a")
    second, reused_second = service._prepare(run, cycle=1, invocation_id="b")
    assert not reused_first
    assert reused_second
    assert first.content_hash == second.content_hash
    assert repository.get_prepared_cycle(RUN_ID, cycle=1) is not None


def test_a_conflicting_prepared_cycle_is_refused(
    service: PilotService, repository: SqliteRepository
):
    run = service.get_run(RUN_ID)
    prepared, _ = service._prepare(run, cycle=1, invocation_id="a")
    altered = prepared.model_copy(
        update={"usage": prepared.usage.model_copy(update={"total_calls": 999})}
    ).sealed()
    with pytest.raises(PreparedCycleConflict, match="different content"):
        repository.store_prepared_cycle(altered)


def test_a_duplicate_cycle_invocation_does_not_advance_twice(service: PilotService):
    service.run_next_cycle(RUN_ID)
    service.run_next_cycle(RUN_ID)
    assert service.get_run(RUN_ID).current_cycle == 2


def test_a_run_cannot_advance_past_the_next_expected_cycle(
    service: PilotService, repository: SqliteRepository
):
    service.run_next_cycle(RUN_ID)
    prepared, _ = service._prepare(service.get_run(RUN_ID), cycle=2, invocation_id="inv")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=4, invocation_id="inv", ttl_seconds=60)
    with pytest.raises(ConcurrentRunUpdate):
        repository.commit_cycle(
            RUN_ID, cycle=4, token=lock.token, content_hash=prepared.content_hash, version=1
        )


# ------------------------------------------------------------------ checkpoints


def test_a_checkpoint_is_idempotent(service: PilotService, repository: SqliteRepository):
    """A scheduler that fires a checkpoint twice must not interview twice."""
    first = service.run_checkpoint(RUN_ID, cycle=0)
    second = service.run_checkpoint(RUN_ID, cycle=0)
    assert len(first) == 6
    assert second == ()
    assert len(repository.get_interviews(RUN_ID, cycle=0)) == 6


def test_an_interview_cannot_be_revised(service: PilotService, repository: SqliteRepository):
    service.run_checkpoint(RUN_ID, cycle=0)
    stored = repository.get_interviews(RUN_ID, cycle=0)[0]
    altered = stored.model_copy(update={"prompt_hash": "sha256:different"}).sealed()
    with pytest.raises(PersistenceError, match="cannot be revised"):
        repository.store_interview(altered)


def test_a_checkpoint_the_run_has_not_reached_is_refused(service: PilotService):
    with pytest.raises(ServiceError, match="is not yet"):
        service.run_checkpoint(RUN_ID, cycle=12)


def test_a_cycle_that_is_not_a_checkpoint_is_refused(service: PilotService):
    with pytest.raises(ServiceError, match="not a checkpoint"):
        service.run_checkpoint(RUN_ID, cycle=3)


def test_interviews_never_touch_arm_state(service: PilotService, repository: SqliteRepository):
    before = {a: s.state_hash for a, s in repository.get_all_current_arm_states(RUN_ID).items()}
    service.run_checkpoint(RUN_ID, cycle=0)
    after = {a: s.state_hash for a, s in repository.get_all_current_arm_states(RUN_ID).items()}
    assert before == after


# ---------------------------------------------------------------------- pause


def test_a_paused_run_refuses_to_advance(service: PilotService, repository: SqliteRepository):
    repository.set_paused(RUN_ID, paused=True)
    with pytest.raises(RunPaused, match="paused"):
        service.run_next_cycle(RUN_ID)
    repository.set_paused(RUN_ID, paused=False)
    assert service.run_next_cycle(RUN_ID).cycle == 1


# ---------------------------------------------------------------------- reset


def test_a_demo_reset_refuses_a_run_that_is_not_local_fixture(
    tmp_path: Path, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    from scripts.local_cli import main

    repository = SqliteRepository(tmp_path / "p.sqlite3")
    configuration = build_configuration(
        pilot_bundle, run_id="run_staging", gateway=pilot_gateway
    ).model_copy(update={"run_kind": RunKind.AWS_STAGING})
    PilotService(repository=repository, bundle=pilot_bundle, gateway=pilot_gateway).create_run(
        run_id="run_staging", configuration=configuration
    )
    repository.close()
    assert (
        main(
            [
                "--database",
                str(tmp_path / "p.sqlite3"),
                "--run-id",
                "run_staging",
                "reset",
            ]
        )
        == 1
    )
