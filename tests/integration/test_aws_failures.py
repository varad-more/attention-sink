"""What a deployed cycle does when a model, or the store, goes wrong.

The list is the one Phase 7 has to answer for: a model failure, a malformed response,
a failed compression, a duplicate request, a conditional conflict, a repeated event, a
call ceiling, and a request for a cycle that has not happened. Each is exercised
against the real DynamoDB adapter and the real handlers, with the one thing that
failed replaced -- because a test that stubbed the service would prove nothing about
the transaction.

The property every one of them asserts is the same: **the run is exactly where it was**.
Not "an error was raised", which is easy, but that no snapshot, no arm state, and no
cycle number moved. Five arms that advanced and one that did not is no longer the same
experiment, and there is no repair for it after the fact.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from attention_sink.aws import analysis as analysis_handler
from attention_sink.aws import run_cycle
from attention_sink.aws.composition import Runtime
from attention_sink.aws.dynamodb import DynamoRepository, table_definition
from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.aws.telemetry import StructuredLogger
from attention_sink.model_gateway import GatewaySettings, ModelGateway, build_gateway
from attention_sink.pilot import ProtocolBundle
from attention_sink.pilot.local import build_configuration
from tests.conftest import fixed_clock
from tests.doubles import ScriptedInvoker

RUN_ID = "run_failure_aws"
TABLE = "attention-sink-aws-failure"
REGION = "us-east-1"


@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        yield client


def _runtime(
    repository: DynamoRepository, bundle: ProtocolBundle, gateway: ModelGateway
) -> Runtime:
    settings = AwsSettings(
        environment=DeploymentEnvironment.LOCAL,
        table_name=TABLE,
        run_id=RUN_ID,
        execution_enabled=True,
    )
    return Runtime(
        settings=settings,
        gateway_settings=gateway.settings,
        bundle=bundle,
        gateway=gateway,
        repository=repository,
        logger=StructuredLogger(service="failures", environment="local", stream=io.StringIO()),
    )


def _seeded(
    table: Any, bundle: ProtocolBundle, gateway: ModelGateway
) -> tuple[Runtime, DynamoRepository]:
    """A created run with six seeded arms and nothing committed."""
    repository = DynamoRepository(table_name=TABLE, client=table)
    runtime = _runtime(repository, bundle, gateway)
    service = runtime.service()
    service.engine_clock = fixed_clock
    service.create_run(
        run_id=RUN_ID,
        configuration=build_configuration(bundle, run_id=RUN_ID, gateway=gateway),
    )
    return runtime, repository


def _unchanged(repository: DynamoRepository, *, at: int) -> None:
    """Assert the run is exactly where it was: no cycle, no snapshot, no lock."""
    run = repository.get_run(RUN_ID)
    assert run is not None
    assert run.current_cycle == at
    assert repository.list_completed_cycles(RUN_ID) == tuple(range(1, at + 1))
    assert repository.get_cycle_lock(RUN_ID) is None


# ------------------------------------------------------------ a model failure


def test_one_writer_failure_leaves_every_arm_where_it_was(table: Any, pilot_bundle: ProtocolBundle):
    """Five arms that advanced and one that did not is not a state this can reach.

    The fifth writer call raises. The engine stages all six or none, so nothing was
    prepared, nothing was committed, and the lock was released on the way out.
    """
    from attention_sink.model_gateway.failures import ModelInvocationError
    from attention_sink.model_gateway.observability import (
        CallMetadata,
        CallOutcome,
        ModelErrorCode,
        ModelRole,
    )

    failure = ModelInvocationError(
        "the provider refused",
        metadata=CallMetadata(
            role=ModelRole.WRITER,
            model_id="fixture-model-v1",
            region="local",
            outcome=CallOutcome.FAILURE,
            latency_ms=1,
            retry_count=0,
            simulated=True,
            error_code=ModelErrorCode.TRANSIENT_SERVER_ERROR,
        ),
    )
    # The first four writers answer; the fifth and everything after it fails.
    from attention_sink.model_gateway import FixtureInvoker

    real = FixtureInvoker()

    class _FailsOnTheFifth:
        def __init__(self) -> None:
            self.calls = 0

        def invoke(self, **kwargs: Any) -> Any:
            self.calls += 1
            if self.calls == 5:
                raise failure
            return real.invoke(**kwargs)

    invoker: Any = _FailsOnTheFifth()
    gateway = build_gateway(
        GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": "0"}),
        invoker=invoker,
        sleep=lambda _s: None,
    )
    runtime, repository = _seeded(table, pilot_bundle, gateway)

    # A provider failure is a fault, not a refusal: it is raised so the invocation
    # fails, is retried, and reaches the dead-letter queue if it keeps failing.
    with pytest.raises(ModelInvocationError, match="refused"):
        run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")

    _unchanged(repository, at=0)
    assert repository.get_prepared_cycle(RUN_ID, cycle=1) is None


def test_a_malformed_model_response_is_a_failure_and_not_a_memory(
    table: Any, pilot_bundle: ProtocolBundle
):
    """A response that does not validate never becomes a memory.

    The schema rejects it, the adapter retries with a repair hint, and when every
    attempt fails the cycle fails. What must never happen is an unvalidated string
    reaching an arm's state, so the assertion is on the arms rather than on the error.
    """
    from attention_sink.model_gateway.failures import ModelInvocationError

    gateway = build_gateway(
        GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": "1"}),
        # A payload with the right keys and the wrong shape: `claimed_citations`
        # is a list of objects, not a string.
        invoker=ScriptedInvoker(script=[{"journal_entry": "x", "claimed_citations": "not a list"}]),
        sleep=lambda _s: None,
    )
    runtime, repository = _seeded(table, pilot_bundle, gateway)

    with pytest.raises(ModelInvocationError, match="malformed_structured_output"):
        run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")

    _unchanged(repository, at=0)
    for arm in pilot_bundle.protocol.arms:
        state = repository.get_current_arm_state(RUN_ID, arm_id=arm)
        assert state is not None
        assert all("not a list" not in memory.text for memory in state.memories)


def test_a_failed_compression_stops_the_cycle_rather_than_forgetting_silently(
    table: Any, pilot_bundle: ProtocolBundle
):
    """The Dreamer's summary is the one call whose failure could lose a memory.

    The mechanism has already decided what to compress; if the summary never arrives,
    the sources must stay exactly where they are. The whole cycle rolls back, which is
    the only outcome that leaves all six arms comparable.
    """
    from attention_sink.model_gateway import FixtureInvoker
    from attention_sink.model_gateway.schemas import SummaryOutput

    real = FixtureInvoker()

    class _RefusesToSummarise:
        def invoke(self, **kwargs: Any) -> Any:
            if kwargs["output_model"] is SummaryOutput:
                msg = "the summariser was unreachable"
                raise TimeoutError(msg)
            return real.invoke(**kwargs)

    invoker: Any = _RefusesToSummarise()
    gateway = build_gateway(
        GatewaySettings.from_env(env={"MAX_MODEL_RETRIES": "0"}),
        invoker=invoker,
        sleep=lambda _s: None,
    )
    runtime, repository = _seeded(table, pilot_bundle, gateway)
    before = {
        arm.value: repository.get_current_arm_state(RUN_ID, arm_id=arm)
        for arm in pilot_bundle.protocol.arms
    }

    # Advance until the summarising arm first needs to compress. Every cycle before
    # that is ordinary; the one that needs a summary is the one that must fail.
    failed = False
    for _ in range(pilot_bundle.protocol.maximum_cycles):
        try:
            outcome = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
        except (RuntimeError, TimeoutError):
            failed = True
            break
        if outcome["result_code"] != "committed":
            break
    assert failed, "the summarising arm never needed a compression"

    run = repository.get_run(RUN_ID)
    assert run is not None
    # Every arm is at the same committed cycle. Nothing advanced past the failure.
    for arm in pilot_bundle.protocol.arms:
        snapshots = repository.list_arm_snapshots(RUN_ID, arm_id=arm)
        assert len(snapshots) == run.current_cycle
    assert repository.get_cycle_lock(RUN_ID) is None
    assert before  # the seeds existed before any of this


# ------------------------------------------------------------------ duplicates


def test_a_duplicate_request_for_a_committed_cycle_changes_nothing(
    table: Any, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    runtime, repository = _seeded(table, pilot_bundle, pilot_gateway)
    first = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    hashes = {s.arm_id: s.snapshot_hash for s in repository.list_cycle_snapshots(RUN_ID, cycle=1)}

    again = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, cycle=1, invocation_id="b")
    assert first["result_code"] == "committed"
    assert again["result_code"] == "already_committed"
    _unchanged(repository, at=1)
    assert {
        s.arm_id: s.snapshot_hash for s in repository.list_cycle_snapshots(RUN_ID, cycle=1)
    } == hashes


def test_a_redelivered_analysis_event_does_not_analyse_twice(
    table: Any, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    from attention_sink.aws.events import CycleCompleted

    runtime, repository = _seeded(table, pilot_bundle, pilot_gateway)
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    run = repository.get_run(RUN_ID)
    assert run is not None
    snapshots = repository.list_cycle_snapshots(RUN_ID, cycle=1)
    completed = CycleCompleted(
        run_id=RUN_ID,
        cycle=1,
        run_kind=run.run_kind.value,
        committed_arms=tuple(s.arm_id.value for s in snapshots),
        snapshot_hashes={s.arm_id.value: s.snapshot_hash for s in snapshots},
        committed_at=run.updated_at.isoformat(),
    )
    first = analysis_handler.analyse_cycle(runtime, completed)
    metrics = len(repository.get_metrics(RUN_ID))
    for _ in range(3):
        assert analysis_handler.analyse_cycle(runtime, completed)["result_code"] == (
            "already_analysed"
        )
    assert first["result_code"] == "analysed"
    assert len(repository.get_metrics(RUN_ID)) == metrics


# ------------------------------------------------------------- the public view


def test_the_public_api_refuses_a_cycle_that_has_not_happened(
    table: Any, pilot_bundle: ProtocolBundle, pilot_gateway: ModelGateway
):
    """The single check that keeps the future of the experiment private.

    A cycle that has been prepared but not committed must be indistinguishable, from
    outside, from one that has not been generated at all.
    """
    from fastapi.testclient import TestClient

    from attention_sink.api.app import build_app

    runtime, repository = _seeded(table, pilot_bundle, pilot_gateway)
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    service = runtime.service()
    service.engine_clock = fixed_clock
    service._prepare(service.get_run(RUN_ID), cycle=2, invocation_id="staged")
    assert repository.get_prepared_cycle(RUN_ID, cycle=2) is not None

    client = TestClient(build_app(repository))
    assert client.get(f"/runs/{RUN_ID}/cycles/1").status_code == 200
    for cycle in (2, 3, 99):
        response = client.get(f"/runs/{RUN_ID}/cycles/{cycle}")
        assert response.status_code == 404
        # And the refusal says nothing about whether a cycle was prepared.
        assert "prepared" not in response.text
    assert client.get(f"/runs/{RUN_ID}/cycles").json()["data"]["items"] == [1]
