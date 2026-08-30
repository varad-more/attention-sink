"""What the DynamoDB adapter does when the store says no.

Split from ``test_dynamodb.py`` because these are not guarantees about the pilot;
they are guarantees about the adapter's honesty under failure. Two of them matter
more than the rest.

An error the adapter does not recognise is **re-raised unchanged**. The alternative --
mapping every ``ClientError`` onto the nearest domain exception -- would turn a
throttle, a missing table, or a credential problem into "the run moved underneath
you", and a wrong error in a run's record is the one a reader will believe.

A transaction that is cancelled between the checks and the write **writes nothing**.
The adapter checks first so that the message is useful, and conditions the writes so
that the check is binding; this proves the second half.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from botocore.exceptions import ClientError
from moto import mock_aws

from attention_sink.aws import keys
from attention_sink.aws.dynamodb import DynamoRepository, _n, _s, table_definition
from attention_sink.domain import ArmId
from attention_sink.model_gateway import ModelGateway
from attention_sink.pilot import ModelUsage, ProtocolBundle, RunStatus
from attention_sink.pilot.local import build_configuration
from attention_sink.pilot.repositories import ConcurrentRunUpdate, PersistenceError
from attention_sink.pilot.service import PilotService
from tests.conftest import fixed_clock

RUN_ID = "run_failure"
TABLE = "attention-sink-failure-test"
REGION = "us-east-1"


def _throttled() -> ClientError:
    """A ClientError the adapter has no mapping for.

    Throttling rather than something exotic, because it is the error a real run is
    most likely to meet and the one whose meaning a wrong label would destroy.
    """
    return ClientError(
        {"Error": {"Code": "ProvisionedThroughputExceededException", "Message": "slow down"}},
        "PutItem",
    )


class _Hostile:
    """A client that refuses one named operation and passes everything else through."""

    def __init__(self, real: Any, operation: str) -> None:
        self._real = real
        self._operation = operation

    def __getattr__(self, name: str) -> Any:
        if name == self._operation:

            def refuse(**_: Any) -> Any:
                raise _throttled()

            return refuse
        return getattr(self._real, name)


def repository_over(client: Any) -> DynamoRepository:
    """A repository over a stand-in client.

    Typed ``Any`` deliberately: a partial fake is exactly what these tests are, and
    mypy's job here is the adapter, not the double.
    """
    return DynamoRepository(table_name=TABLE, client=client)


@pytest.fixture
def repository(monkeypatch: pytest.MonkeyPatch) -> Iterator[DynamoRepository]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        yield DynamoRepository(table_name=TABLE, client=client)


@pytest.fixture
def service(
    repository: DynamoRepository, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
) -> PilotService:
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


# ------------------------------------------------------- unrecognised failures


@pytest.mark.parametrize(
    ("operation", "call"),
    [
        ("put_item", lambda r: r.store_token_count(counter_version="v1", text_hash="h", tokens=1)),
        ("update_item", lambda r: r.set_paused(RUN_ID, paused=True)),
        (
            "update_item",
            lambda r: r.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="x", ttl_seconds=60),
        ),
        ("update_item", lambda r: r.release_cycle_lock(RUN_ID, token="whatever")),  # noqa: S106
        (
            "update_item",
            lambda r: r.update_run_status(RUN_ID, status=RunStatus.RUNNING, version=0),
        ),
        ("put_item", lambda r: r.mark_cycle_analysed(RUN_ID, cycle=1, detail={})),
        ("update_item", lambda r: r.add_usage(RUN_ID, usage=ModelUsage(total_calls=1))),
    ],
)
@pytest.mark.usefixtures("service")
def test_an_error_the_adapter_does_not_recognise_is_re_raised_unchanged(
    repository: DynamoRepository, operation: str, call: Any
):
    hostile = repository_over(_Hostile(repository.client, operation))
    with pytest.raises(ClientError) as raised:
        call(hostile)
    assert raised.value.response["Error"]["Code"] == "ProvisionedThroughputExceededException"


@pytest.mark.usefixtures("service")
def test_a_refused_seed_that_is_not_a_conflict_is_re_raised(repository: DynamoRepository):
    state = repository.get_current_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO)
    assert state is not None
    hostile = repository_over(_Hostile(repository.client, "put_item"))
    with pytest.raises(ClientError):
        hostile.seed_arm_state(RUN_ID, arm_id=ArmId.ARM_LRU, state=state)


def test_a_refused_run_creation_that_is_not_a_conflict_is_re_raised(
    repository: DynamoRepository, service: PilotService
):
    hostile = repository_over(_Hostile(repository.client, "put_item"))
    with pytest.raises(ClientError):
        hostile.create_run(service.get_run(RUN_ID))


@pytest.mark.usefixtures("service")
def test_a_refused_interview_that_is_not_a_conflict_is_re_raised(
    repository: DynamoRepository, service: PilotService
):
    service.run_checkpoint(RUN_ID, cycle=0)
    stored = repository.get_interviews(RUN_ID, cycle=0)[0]
    hostile = repository_over(_Hostile(repository.client, "put_item"))
    with pytest.raises(ClientError):
        hostile.store_interview(stored)


def test_usage_that_will_not_settle_says_so_rather_than_losing_the_count(
    repository: DynamoRepository, service: PilotService
):
    """A spend counter that undercounts is worse than one that fails.

    The condition is contended by bumping the sequence out of band on every attempt,
    which is what two invocations recording checkpoint interviews at once would do.
    """
    del service
    real = repository.client

    class _AlwaysContended:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def update_item(self, **kwargs: Any) -> Any:
            real.update_item(
                TableName=TABLE,
                Key={"PK": _s(keys.run_pk(RUN_ID)), "SK": _s(keys.META_SK)},
                UpdateExpression="SET usage_seq = usage_seq + :one",
                ExpressionAttributeValues={":one": _n(1)},
            )
            return real.update_item(**kwargs)

    contended = repository_over(_AlwaysContended())
    with pytest.raises(PersistenceError, match="contended"):
        contended.add_usage(RUN_ID, usage=ModelUsage(total_calls=1))


# ------------------------------------------------------------- the transaction


def test_a_cancelled_transaction_leaves_no_partial_cycle(
    repository: DynamoRepository, service: PilotService
):
    """Another invocation advances the run between the checks and the write.

    The pre-checks are what produce a useful message; the conditions inside the
    transaction are what make the checks binding. This proves the second half: five
    arms that advanced and one that did not is not a state this store can reach.
    """
    run = service.get_run(RUN_ID)
    prepared, _ = service._prepare(run, cycle=1, invocation_id="slow")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="slow", ttl_seconds=60)
    real = repository.client

    class _RaceAtTheLastMoment:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def transact_write_items(self, **kwargs: Any) -> Any:
            # Somebody else advances the run after the checks passed.
            real.update_item(
                TableName=TABLE,
                Key={"PK": _s(keys.run_pk(RUN_ID)), "SK": _s(keys.META_SK)},
                UpdateExpression="SET #v = #v + :one",
                ExpressionAttributeNames={"#v": "version"},
                ExpressionAttributeValues={":one": _n(1)},
            )
            return real.transact_write_items(**kwargs)

    racing = repository_over(_RaceAtTheLastMoment())
    with pytest.raises(ConcurrentRunUpdate, match="was cancelled"):
        racing.commit_cycle(
            RUN_ID,
            cycle=1,
            token=lock.token,
            content_hash=prepared.content_hash,
            version=run.version,
        )
    assert repository.list_completed_cycles(RUN_ID) == ()
    assert repository.list_cycle_snapshots(RUN_ID, cycle=1) == ()
    assert repository.get_current_arm_state(RUN_ID, arm_id=ArmId.ARM_FIFO) is not None


def test_a_transaction_failure_that_is_not_a_cancellation_is_re_raised(
    repository: DynamoRepository, service: PilotService
):
    run = service.get_run(RUN_ID)
    prepared, _ = service._prepare(run, cycle=1, invocation_id="a")
    lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="a", ttl_seconds=60)
    hostile = repository_over(_Hostile(repository.client, "transact_write_items"))
    with pytest.raises(ClientError):
        hostile.commit_cycle(
            RUN_ID,
            cycle=1,
            token=lock.token,
            content_hash=prepared.content_hash,
            version=run.version,
        )


def test_a_commit_against_a_run_that_does_not_exist_is_refused(repository: DynamoRepository):
    with pytest.raises(PersistenceError, match="no run"):
        repository.commit_cycle(
            RUN_ID + "_absent",
            cycle=1,
            token="x" * 32,
            content_hash="sha256:none",
            version=0,
        )


def test_storing_the_same_prepared_cycle_twice_returns_the_stored_one(
    repository: DynamoRepository, service: PilotService
):
    """The retry path. Two invocations that staged the same experiment agree."""
    run = service.get_run(RUN_ID)
    prepared, _ = service._prepare(run, cycle=1, invocation_id="a")
    again = repository.store_prepared_cycle(prepared)
    assert again.content_hash == prepared.content_hash
    assert again.invocation_id == prepared.invocation_id


# ------------------------------------------------------------------ pagination


def test_a_query_follows_every_page(repository: DynamoRepository, service: PilotService):
    """A truncated page is a silently short answer, not an error.

    DynamoDB caps a query at one megabyte and hands back a continuation key. An
    adapter that ignored it would return the first eight hundred snapshots of a run
    and no indication that there were more.
    """
    for _ in range(2):
        service.run_next_cycle(RUN_ID)
    real = repository.client
    pages: list[int] = []

    class _OnePageAtATime:
        def __getattr__(self, name: str) -> Any:
            return getattr(real, name)

        def query(self, **kwargs: Any) -> Any:
            response = real.query(**{**kwargs, "Limit": 1})
            pages.append(len(response.get("Items", ())))
            return response

    paged = repository_over(_OnePageAtATime())
    assert paged.list_cycle_snapshots(RUN_ID, cycle=1) != ()
    assert len(paged.list_cycle_snapshots(RUN_ID, cycle=1)) == 6
    assert len(pages) > 6
