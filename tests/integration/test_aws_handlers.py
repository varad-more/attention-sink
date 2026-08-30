"""The three Lambda handlers, over moto.

Handlers are thin, so what is tested here is not the experiment -- Phases 4 to 6 test
that -- but the decisions a deployment makes around it: whether it is armed, what a
duplicate invocation means, what happens when an event is malformed or describes a
cycle that did not commit, and whether the public API can be made to write.
"""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from typing import Any

import boto3
import pytest
from moto import mock_aws

from attention_sink.aws import analysis as analysis_handler
from attention_sink.aws import run_cycle
from attention_sink.aws.composition import Runtime
from attention_sink.aws.dynamodb import DynamoRepository, table_definition
from attention_sink.aws.events import CYCLE_COMPLETED_SOURCE, CycleCompleted
from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.aws.telemetry import StructuredLogger
from attention_sink.model_gateway import GatewaySettings, ModelGateway, build_gateway
from attention_sink.pilot import ProtocolBundle
from attention_sink.pilot.local import build_configuration
from tests.conftest import fixed_clock

RUN_ID = "run_handler"
TABLE = "attention-sink-handler-test"
REGION = "us-east-1"


class _Context:
    """Just enough of a Lambda context to carry a request identifier."""

    aws_request_id = "req-0001"


@pytest.fixture
def aws_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


@pytest.fixture
def logs() -> io.StringIO:
    return io.StringIO()


def _runtime(
    repository: DynamoRepository,
    bundle: ProtocolBundle,
    gateway: ModelGateway,
    stream: io.StringIO,
    **overrides: Any,
) -> Runtime:
    settings = AwsSettings(
        **{
            "environment": DeploymentEnvironment.LOCAL,
            "table_name": TABLE,
            "run_id": RUN_ID,
            "execution_enabled": True,
            **overrides,
        }
    )
    return Runtime(
        settings=settings,
        gateway_settings=gateway.settings,
        bundle=bundle,
        gateway=gateway,
        repository=repository,
        logger=StructuredLogger(
            service="test", environment=settings.environment.value, stream=stream
        ),
    )


@pytest.fixture
def runtime(
    aws_environment: None,
    pilot_bundle: ProtocolBundle,
    logs: io.StringIO,
) -> Iterator[Runtime]:
    """A whole deployment in one process: table, bus, fixture gateway, run created."""
    del aws_environment
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        repository = DynamoRepository(table_name=TABLE, client=client)
        gateway = build_gateway(GatewaySettings.from_env(env={}))
        built = _runtime(repository, pilot_bundle, gateway, logs)
        service = built.service()
        service.engine_clock = fixed_clock
        service.create_run(
            run_id=RUN_ID,
            configuration=build_configuration(pilot_bundle, run_id=RUN_ID, gateway=gateway),
        )
        yield built


def _lines(stream: io.StringIO) -> list[dict[str, Any]]:
    return [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]


# --------------------------------------------------------------- the run cycle


def test_one_invocation_advances_exactly_one_cycle(runtime: Runtime):
    result = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    assert result["result_code"] == "committed"
    assert result["cycle"] == 1
    assert len(result["committed_arms"]) == 6
    assert runtime.repository.list_completed_cycles(RUN_ID) == (1,)


def test_a_disabled_deployment_refuses_without_raising(
    runtime: Runtime, pilot_bundle: ProtocolBundle, logs: io.StringIO
):
    """A scheduler firing at a disabled stack is configured behaviour, not a fault.

    Raising would send it to the dead-letter queue and have an operator investigate a
    deployment doing exactly what it was told.
    """
    disabled = _runtime(
        runtime.repository, pilot_bundle, runtime.gateway, logs, execution_enabled=False
    )
    result = run_cycle.run_one_cycle(disabled, run_id=RUN_ID, invocation_id="a")
    assert result["result_code"] == "execution_disabled"
    assert runtime.repository.list_completed_cycles(RUN_ID) == ()


def test_a_paused_run_is_reported_rather_than_advanced(runtime: Runtime):
    runtime.repository.set_paused(RUN_ID, paused=True)
    result = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    assert result["result_code"] == "run_paused"
    assert runtime.repository.list_completed_cycles(RUN_ID) == ()


def test_a_run_that_does_not_exist_is_reported(runtime: Runtime):
    result = run_cycle.run_one_cycle(runtime, run_id="run_absent", invocation_id="a")
    assert result["result_code"] == "run_not_found"


def test_an_invocation_naming_a_committed_cycle_returns_it_rather_than_repeating_it(
    runtime: Runtime,
):
    """Idempotency for a retried invocation.

    A scheduler tick names no cycle and means "the next one"; a retry names one and
    means "this one".
    """
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    again = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, cycle=1, invocation_id="b")
    assert again["result_code"] == "already_committed"
    assert len(again["committed_arms"]) == 6
    run = runtime.repository.get_run(RUN_ID)
    assert run is not None
    assert run.current_cycle == 1


def test_a_lock_held_elsewhere_stops_this_invocation_without_a_fault(runtime: Runtime):
    runtime.repository.acquire_cycle_lock(
        RUN_ID, cycle=1, invocation_id="somebody-else", ttl_seconds=300
    )
    result = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    assert result["result_code"] == "lock_held_elsewhere"


def test_an_environment_ceiling_stops_the_run_below_the_protocol_maximum(
    runtime: Runtime, pilot_bundle: ProtocolBundle, logs: io.StringIO
):
    """What makes arming staging by mistake cost two cycles rather than twenty-four."""
    capped = _runtime(
        runtime.repository,
        pilot_bundle,
        runtime.gateway,
        logs,
        execution_enabled=True,
        maximum_cycles=2,
    )
    for _ in range(3):
        result = run_cycle.run_one_cycle(capped, run_id=RUN_ID, invocation_id="a")
    assert result["result_code"] == "run_complete"
    final = capped.repository.get_run(RUN_ID)
    assert final is not None
    assert final.current_cycle == 2


def test_a_committed_cycle_is_announced_on_the_bus(runtime: Runtime, monkeypatch):
    """The event carries identifiers and digests, and no content."""
    published: list[dict[str, Any]] = []

    class _Bus:
        def put_events(self, *, Entries: list[dict[str, Any]]) -> dict[str, Any]:
            published.extend(Entries)
            return {"FailedEntryCount": 0}

    monkeypatch.setattr(Runtime, "events", lambda _self: _Bus())
    named = _runtime(
        runtime.repository,
        runtime.bundle,
        runtime.gateway,
        io.StringIO(),
        # A named bus rather than the account default, which is the deployed shape.
        event_bus_name="attention-sink-staging",
    )
    run_cycle.run_one_cycle(named, run_id=RUN_ID, invocation_id="a")
    (entry,) = published
    assert entry["EventBusName"] == "attention-sink-staging"
    assert entry["Source"] == CYCLE_COMPLETED_SOURCE
    detail = json.loads(entry["Detail"])
    assert detail["cycle"] == 1
    assert set(detail["snapshot_hashes"]) == set(detail["committed_arms"])
    assert "journal_entry" not in entry["Detail"]


def test_the_log_line_names_the_result_code_and_no_content(runtime: Runtime, logs: io.StringIO):
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a", request_id="req-1")
    committed = [line for line in _lines(logs) if line.get("result_code") == "committed"]
    assert committed
    assert committed[0]["run_id"] == RUN_ID
    assert committed[0]["cycle"] == 1
    assert "journal_entry" not in json.dumps(committed[0])


# ----------------------------------------------------------------- the analysis


def _completed(runtime: Runtime, cycle: int, **overrides: Any) -> CycleCompleted:
    run = runtime.repository.get_run(RUN_ID)
    assert run is not None
    snapshots = runtime.repository.list_cycle_snapshots(RUN_ID, cycle=cycle)
    return CycleCompleted(
        run_id=RUN_ID,
        cycle=cycle,
        run_kind=run.run_kind.value,
        committed_arms=tuple(s.arm_id.value for s in snapshots),
        snapshot_hashes={s.arm_id.value: s.snapshot_hash for s in snapshots},
        checkpoint=run.configuration.is_checkpoint(cycle),
        run_complete=run.is_complete,
        committed_at=run.updated_at.isoformat(),
        **overrides,
    )


def test_analysis_scores_a_committed_cycle_and_stores_its_artifacts(runtime: Runtime):
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    result = analysis_handler.analyse_cycle(runtime, _completed(runtime, 1))
    assert result["result_code"] == "analysed"
    assert result["metrics"] > 0
    for name in ("divergence", "echoes", "contradictions", "question_scores"):
        assert runtime.repository.get_analysis_artifact(RUN_ID, name=name) is not None


def test_a_redelivered_event_costs_one_read_and_changes_nothing(runtime: Runtime):
    """EventBridge delivers at least once, so this is the normal case, not the edge."""
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    completed = _completed(runtime, 1)
    first = analysis_handler.analyse_cycle(runtime, completed)
    second = analysis_handler.analyse_cycle(runtime, completed)
    assert first["result_code"] == "analysed"
    assert second["result_code"] == "already_analysed"


def test_analysis_refuses_a_cycle_the_store_does_not_have(runtime: Runtime):
    """An event is a notification; the store is the record."""
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    future = _completed(runtime, 1).model_copy(update={"cycle": 9})
    result = analysis_handler.analyse_cycle(runtime, future)
    assert result["result_code"] == "cycle_not_committed"


def test_analysis_refuses_a_cycle_whose_content_differs_from_the_event(runtime: Runtime):
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    completed = _completed(runtime, 1)
    lying = completed.model_copy(
        update={"snapshot_hashes": dict.fromkeys(completed.snapshot_hashes, "sha256:other")}
    )
    result = analysis_handler.analyse_cycle(runtime, lying)
    assert result["result_code"] == "cycle_not_committed"
    assert "different content" in result["reason"]


def test_a_failed_analysis_releases_its_claim_so_a_retry_can_run(runtime: Runtime, monkeypatch):
    """A crash must not leave a cycle marked analysed and permanently unanalysed."""
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    completed = _completed(runtime, 1)

    class _Unreachable:
        def analyse_run(self, run_id: str) -> Any:
            raise RuntimeError(f"the evaluator was unreachable for {run_id}")

    monkeypatch.setattr(Runtime, "analysis", lambda _self: _Unreachable())
    with pytest.raises(RuntimeError, match="unreachable"):
        analysis_handler.analyse_cycle(runtime, completed)
    assert runtime.repository.get_cycle_analysis(RUN_ID, cycle=1) is None


def test_a_malformed_event_is_a_permanent_failure(runtime: Runtime, monkeypatch):
    """A redelivery of a malformed event is malformed too: the queue, not a retry."""
    monkeypatch.setattr(analysis_handler, "build_runtime", lambda _name: runtime)
    with pytest.raises(ValueError, match="not a cycle-completed event"):
        analysis_handler.handler({"detail": {"nonsense": True}}, _Context())


def test_a_checkpoint_missed_by_the_cycle_handler_is_taken_by_analysis(
    aws_environment: None, pilot_bundle: ProtocolBundle, logs: io.StringIO
):
    """The case this covers: the cycle Lambda committed and then ran out of time.

    Committed through the repository directly rather than through
    ``run_next_cycle``, because that method interviews inline -- which is exactly the
    step this test needs not to have happened.
    """
    del aws_environment
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        repository = DynamoRepository(table_name=TABLE, client=client)
        gateway = build_gateway(GatewaySettings.from_env(env={}))
        runtime = _runtime(repository, pilot_bundle, gateway, logs)
        service = runtime.service()
        service.engine_clock = fixed_clock
        configuration = build_configuration(
            pilot_bundle, run_id=RUN_ID, gateway=gateway
        ).model_copy(update={"checkpoint_cycles": (0, 1)})
        service.create_run(run_id=RUN_ID, configuration=configuration)

        run = service.get_run(RUN_ID)
        prepared, _ = service._prepare(run, cycle=1, invocation_id="died")
        lock = repository.acquire_cycle_lock(RUN_ID, cycle=1, invocation_id="died", ttl_seconds=60)
        repository.commit_cycle(
            RUN_ID,
            cycle=1,
            token=lock.token,
            content_hash=prepared.content_hash,
            version=run.version,
        )
        assert repository.get_interviews(RUN_ID, cycle=1) == ()

        result = analysis_handler.analyse_cycle(runtime, _completed(runtime, 1))
        assert result["checkpoint_interviews"] == 6
        assert len(repository.get_interviews(RUN_ID, cycle=1)) == 6


# ------------------------------------------------------------------ the read API


def test_the_public_api_registers_no_mutating_route(runtime: Runtime):
    """Read-only stays a property of the application, checked a third time here."""
    from attention_sink.api.app import build_app, registered_methods

    app = build_app(runtime.repository, allowed_origins=("https://example.test",))
    assert registered_methods(app) == {"GET"}


def test_the_public_api_serves_committed_cycles_and_hides_prepared_ones(runtime: Runtime):
    from fastapi.testclient import TestClient

    from attention_sink.api.app import build_app

    service = runtime.service()
    service.engine_clock = fixed_clock
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    service._prepare(service.get_run(RUN_ID), cycle=2, invocation_id="staged")

    client = TestClient(build_app(runtime.repository))
    assert client.get(f"/runs/{RUN_ID}/cycles").json()["data"]["items"] == [1]
    assert client.get(f"/runs/{RUN_ID}/cycles/1").status_code == 200
    assert client.get(f"/runs/{RUN_ID}/cycles/2").status_code == 404


def test_the_read_api_lambda_serves_one_http_api_event(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
):
    """The whole read handler: one API Gateway event in, one response out.

    The application underneath is the same `build_app` the local process serves, so
    what is worth testing here is only the translation -- that a v2 payload reaches a
    route and comes back as a v2 response.
    """
    from attention_sink.aws import read_api

    monkeypatch.setattr(read_api, "build_runtime", lambda _name: runtime)
    monkeypatch.setattr(read_api, "_HANDLER", None)
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")

    def request() -> Any:
        return read_api.handler(
            {
                "version": "2.0",
                "routeKey": "GET /{proxy+}",
                "rawPath": f"/runs/{RUN_ID}/cycles",
                "rawQueryString": "",
                "headers": {"host": "example.execute-api.us-east-1.amazonaws.com"},
                "requestContext": {
                    "http": {
                        "method": "GET",
                        "path": f"/runs/{RUN_ID}/cycles",
                        "protocol": "HTTP/1.1",
                        "sourceIp": "203.0.113.1",
                    },
                    "stage": "$default",
                    "requestId": "req-1",
                },
                "isBase64Encoded": False,
            },
            _Context(),
        )

    response = request()
    assert response["statusCode"] == 200
    assert json.loads(response["body"])["data"]["items"] == [1]
    # The adapter is built once per execution environment and reused after that.
    assert request()["statusCode"] == 200


def test_the_run_cycle_lambda_entry_point_reads_its_event(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
):
    """The handler itself, not just the function under it."""
    monkeypatch.setattr(run_cycle, "build_runtime", lambda _name: runtime)
    result = run_cycle.handler({"run_id": RUN_ID}, _Context())
    assert result["result_code"] == "committed"
    # A payload that is not a mapping still means "advance the configured run".
    assert run_cycle.handler(None, _Context())["cycle"] == 2


def test_a_cycle_that_exceeds_the_model_call_ceiling_stops_without_spending_more(
    aws_environment: None, pilot_bundle: ProtocolBundle, logs: io.StringIO
):
    """Raised before the call, so nothing was spent and no arm advanced.

    Its own result code because it is the one failure an operator answers by changing
    the protocol rather than by looking for a bug -- and because the CloudWatch alarm
    is a metric filter on exactly that code.
    """
    del aws_environment
    with mock_aws():
        client = boto3.client("dynamodb", region_name=REGION)
        client.create_table(**table_definition(TABLE))
        repository = DynamoRepository(table_name=TABLE, client=client)
        gateway = build_gateway(GatewaySettings.from_env(env={}))
        runtime = _runtime(repository, pilot_bundle, gateway, logs)
        service = runtime.service()
        service.engine_clock = fixed_clock
        limits = pilot_bundle.protocol.model_call_limits.model_copy(
            update={"max_model_calls_per_run": 1}
        )
        configuration = build_configuration(
            pilot_bundle, run_id=RUN_ID, gateway=gateway
        ).model_copy(update={"model_call_limits": limits})
        service.create_run(run_id=RUN_ID, configuration=configuration)

        result = run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
        assert result["result_code"] == "model_call_limit"
        assert repository.list_completed_cycles(RUN_ID) == ()
        run = repository.get_run(RUN_ID)
        assert run is not None
        assert run.current_cycle == 0


def test_a_genuine_fault_is_raised_so_it_reaches_the_dead_letter_queue(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
):
    """A fault is not a refusal. Returning a status for one would hide an outage."""
    from attention_sink.pilot.service import ServiceError

    class _Broken:
        def get_run(self, run_id: str) -> Any:
            return runtime.repository.get_run(run_id)

        def run_next_cycle(self, run_id: str, *, invocation_id: str) -> Any:
            raise ServiceError(f"the store went away during {run_id}/{invocation_id}")

    monkeypatch.setattr(Runtime, "service", lambda _self: _Broken())
    with pytest.raises(RuntimeError, match="cycle of .* failed"):
        run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")


def test_the_analysis_lambda_entry_point_parses_an_eventbridge_event(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(analysis_handler, "build_runtime", lambda _name: runtime)
    run_cycle.run_one_cycle(runtime, run_id=RUN_ID, invocation_id="a")
    event = {
        "source": CYCLE_COMPLETED_SOURCE,
        "detail-type": "CycleCompleted",
        "detail": _completed(runtime, 1).model_dump(mode="json"),
    }
    assert analysis_handler.handler(event, _Context())["result_code"] == "analysed"


def test_a_runtime_builds_the_clients_the_handlers_reach_for(runtime: Runtime):
    """Built per call, from the default credential chain, and never memoised.

    Under moto these are stand-ins; what is asserted is that the runtime hands out a
    client at all, because a handler that could not get one would fail at the moment
    it published an event rather than at cold start.
    """
    assert runtime.s3() is not None
    assert runtime.events() is not None
