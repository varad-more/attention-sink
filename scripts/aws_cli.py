#!/usr/bin/env python
"""The operator commands for a deployed stack.

The AWS composition root, and the counterpart of ``scripts/local_cli.py``. It lives
outside the packages for the same reason that one does: choosing an adapter is
composition, and an application that imported its own infrastructure would have no
adapter line left.

Every command that could spend money or change a run says what it is about to do and
refuses unless the deployment is armed for it. There is no command here that enables
the scheduler and creates a run in one step, because the two mistakes those would
combine are the two most expensive ones available.

    python scripts/aws_cli.py preflight
    python scripts/aws_cli.py bootstrap
    python scripts/aws_cli.py status
    python scripts/aws_cli.py cycle
    python scripts/aws_cli.py execution inspect | enable | disable
    python scripts/aws_cli.py schedule inspect | enable | disable | invoke-once
    python scripts/aws_cli.py export --out s3
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

from attention_sink.aws.composition import Runtime, build_runtime
from attention_sink.aws.dynamodb import DynamoRepository
from attention_sink.aws.exports import S3ExportStorage
from attention_sink.aws.settings import AwsSettings, DeploymentEnvironment
from attention_sink.model_gateway import (
    ConfigurationError,
    GatewaySettings,
    ModelMode,
    TokenCountSource,
)
from attention_sink.pilot.cli import model_specs
from attention_sink.pilot.configuration import PilotRunConfiguration
from attention_sink.protocol import current_version

__all__ = ["main"]

SERVICE_NAME = "operator"


def _mask(account: str) -> str:
    """Show the last four digits of an account identifier and nothing more.

    A full account number in a terminal transcript is not a credential, but it is a
    target, and there is no command here that needs the whole of it.
    """
    return f"****{account[-4:]}" if len(account) >= 4 else "****"


def _runtime() -> Runtime:
    return build_runtime(SERVICE_NAME)


# ------------------------------------------------------------------- preflight


def _command_preflight(args: argparse.Namespace) -> int:
    """Prove who we are, where we are, and that nothing is armed.

    Run before every deployment. Seven checks, and any one of them failing stops the
    deployment: an account nobody meant to deploy to is the mistake that is hardest
    to notice and most expensive to undo.
    """
    del args
    failures: list[str] = []
    print("credential preflight")
    print("-" * 60)

    profile = os.environ.get("AWS_PROFILE", "").strip()
    region = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "")).strip()
    print(f"  profile                {profile or '(default chain)'}")
    print(f"  region                 {region or '(unset)'}")
    if not region:
        failures.append("AWS_REGION is not set; a Region must be chosen deliberately")

    try:
        identity = boto3.Session().client("sts").get_caller_identity()
    except (ClientError, BotoCoreError, NoCredentialsError) as exc:
        print(f"  caller identity        FAILED: {type(exc).__name__}")
        failures.append(f"caller identity could not be verified: {exc}")
        identity = {}
    else:
        print(f"  account                {_mask(identity['Account'])}")
        print(f"  caller                 {identity['Arn'].rsplit('/', 1)[-1]}")

    # The first deployment of a new stack has no table name yet: it is a stack
    # output. Preflight still has to run before that deploy -- checking the account,
    # the Region, the models, and the switches is exactly what it is for -- so an
    # absent table reads as "not deployed yet" and every other check still runs.
    deployed = bool(os.environ.get("AS_TABLE_NAME", "").strip())
    try:
        settings = AwsSettings.from_env(
            env={**os.environ, "AS_TABLE_NAME": os.environ.get("AS_TABLE_NAME") or "pending"}
        )
    except ConfigurationError as exc:
        print(f"  deployment settings    FAILED: {exc}")
        return _report([*failures, str(exc)])

    print(f"  environment            {settings.environment.value}")
    print(f"  run                    {settings.run_id}")
    print(f"  table                  {settings.table_name if deployed else '(not deployed yet)'}")
    if settings.environment is DeploymentEnvironment.PRODUCTION:
        failures.append("AS_DEPLOYMENT_ENVIRONMENT is production; Phase 7 deploys staging only")

    print(f"  execution enabled      {settings.execution_enabled}")
    print(f"  bedrock calls allowed  {settings.allow_bedrock_calls}")
    print(f"  canonical              {settings.canonical}")
    if settings.canonical:
        failures.append("this deployment is marked canonical; Phase 7 creates no canonical run")

    try:
        gateway_settings = GatewaySettings.from_env()
    except ConfigurationError as exc:
        print(f"  model configuration    FAILED: {exc}")
        return _report([*failures, str(exc)])

    print(f"  model mode             {gateway_settings.mode.value}")
    if gateway_settings.mode is ModelMode.BEDROCK and identity:
        failures.extend(_check_model_access(gateway_settings))
    elif settings.environment is not DeploymentEnvironment.LOCAL:
        failures.append("a deployed environment requires MODEL_MODE=bedrock")

    failures.extend(_check_scheduler(settings))
    return _report(failures)


def _check_model_access(gateway_settings: GatewaySettings) -> list[str]:
    """Confirm every configured model is visible to this account and Region.

    Visibility, not invocability: proving a model can be invoked means invoking it,
    which costs money and is what the smoke test is for. What this catches is the
    common failure -- a model identifier that is not available in this Region, or
    access that was never requested in the console.
    """
    models = gateway_settings.models
    if models is None:  # pragma: no cover - bedrock mode guarantees a configuration
        return ["bedrock mode reached the preflight without a model configuration"]
    wanted = {
        "writer": models.writer_model_id,
        "auditor": models.auditor_model_id,
        "judge": models.judge_model_id,
        "summary": models.summary_model_id,
        "embedding": models.embedding_model_id,
    }
    try:
        client = boto3.Session().client("bedrock", region_name=models.region)
        available = {
            entry["modelId"] for entry in client.list_foundation_models()["modelSummaries"]
        }
    except (ClientError, BotoCoreError) as exc:
        return [f"the Bedrock model list could not be read: {exc}"]

    failures: list[str] = []
    for role, model_id in wanted.items():
        # An inference-profile identifier is prefixed with a Region group and is not
        # in the foundation-model list under that name.
        base = model_id.split(".", 1)[-1] if model_id[:3] in {"us.", "eu.", "ap."} else model_id
        ok = model_id in available or base in available
        print(f"  model {role:<16} {model_id} {'ok' if ok else 'NOT AVAILABLE'}")
        if not ok:
            failures.append(f"{role} model {model_id} is not available in {models.region}")
    return failures


def _check_scheduler(settings: AwsSettings) -> list[str]:
    """Confirm the schedule exists and is not running.

    Reported rather than fixed. A preflight that silently disabled a schedule would
    be a preflight that hid somebody deliberately enabling one.
    """
    name = f"attention-sink-{settings.environment.value}-cycle"
    try:
        schedule = boto3.Session().client("scheduler").get_schedule(Name=name)
    except (ClientError, BotoCoreError) as exc:
        print(f"  schedule               not deployed yet ({type(exc).__name__})")
        del exc
        return []
    state = schedule["State"]
    print(f"  schedule               {name} {state}")
    return [] if state == "DISABLED" else [f"schedule {name} is {state}; disable it first"]


def _report(failures: Sequence[str]) -> int:
    print("-" * 60)
    if failures:
        print(f"PREFLIGHT FAILED ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
        return 1
    print("preflight passed: account, Region, models, and switches all confirmed")
    return 0


# ------------------------------------------------------------------- bootstrap


def _command_bootstrap(args: argparse.Namespace) -> int:
    """Create the staging run, from the locally validated protocol.

    Six identical seed states at cycle 0, real models recorded on the configuration,
    and nothing generated. No fixture output is copied in: a staging run exists to
    find out what the real models do, and seeding it with fabrications would answer
    that question with the fixtures' answer.
    """
    runtime = _runtime()
    settings = runtime.settings
    if runtime.gateway.simulated:
        print(
            "FAILED: this process has a fixture gateway; a staging run must record "
            "the models it actually used",
            file=sys.stderr,
        )
        return 1

    configuration = _staging_configuration(runtime, run_id=args.run_id or settings.run_id)
    configuration.require_run_kind_consistent()
    service = runtime.service()
    try:
        service.create_run(run_id=configuration.run_id, configuration=configuration)
    except Exception as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1

    print(f"created {configuration.run_id} [{configuration.run_kind.value}]")
    print(f"  arms                 {len(configuration.arms)}")
    print(f"  cycles               {configuration.maximum_cycles}")
    print(f"  environment ceiling  {settings.maximum_cycles or '(protocol maximum)'}")
    print(
        f"  budget               {configuration.memory_budget_tokens} "
        f"({configuration.token_count_source})"
    )
    print(f"  writer model         {configuration.writer_model.model_id}")
    print(f"  embedding model      {configuration.embedding_model.model_id}")
    print(f"  prompt set           {configuration.prompt_set_digest}")
    print(f"  protocol             {configuration.protocol_version}")
    print(f"  canonical            {configuration.canonical}")
    print(f"  execution enabled    {settings.execution_enabled}")

    if not args.interview:
        print("  cycle-0 interviews   not taken (pass --interview; costs six calls)")
        return 0
    settings.require_can_execute()
    records = service.run_checkpoint(configuration.run_id, cycle=0)
    print(f"  cycle-0 interviews   {len(records)}")
    return 0


TOKEN_COUNT_SOURCE_NAMES: dict[TokenCountSource, str] = {
    TokenCountSource.BEDROCK: "bedrock_count_tokens",
    TokenCountSource.HEURISTIC: "approximate_heuristic",
}
"""What a run records about the counter its budget is denominated in.

Read off the gateway that was actually built, not off the protocol's declared value.
The protocol says ``local_fixture_heuristic``, which is true of a local run and would
be misleading on a deployed one: the counter is the same, the run is not a fixture.
Only ``bedrock_count_tokens`` is in ``EXACT_TOKEN_COUNT_SOURCES``, so a canonical run
denominated in the other is refused whichever name it carries (ADR-012).
"""


def _staging_configuration(runtime: Runtime, *, run_id: str) -> PilotRunConfiguration:
    """Resolve the configuration one staging run is defined by."""
    bundle = runtime.bundle
    bundle.require_runnable(canonical=runtime.settings.canonical)
    writer, embedding = model_specs(runtime.gateway)
    version = current_version()
    return PilotRunConfiguration.from_bundle(
        bundle,
        run_id=run_id,
        created_at=datetime.now(UTC),
        writer_model=writer,
        embedding_model=embedding,
        prompt_set_digest=runtime.gateway.prompts.prompt_set_digest(
            bundle.protocol.writer_prompt_version
        ),
        app_version=version.app_version,
        git_commit=version.git_commit,
        run_kind=runtime.settings.run_kind,
        token_count_source=TOKEN_COUNT_SOURCE_NAMES[runtime.gateway_settings.token_count_source],
    )


# ---------------------------------------------------------------------- status


def _command_status(args: argparse.Namespace) -> int:
    """Where the run has got to, and what it has spent."""
    runtime = _runtime()
    run_id = args.run_id or runtime.settings.run_id
    run = runtime.repository.get_run(run_id)
    if run is None:
        print(f"no run {run_id}", file=sys.stderr)
        return 1
    print(f"{run.run_id} [{run.run_kind.value}] {run.status.value}")
    print(f"  cycle              {run.current_cycle}/{run.configuration.maximum_cycles}")
    print(f"  version            {run.version}   paused={run.paused}")
    print(f"  model calls        {run.usage.total_calls} {run.usage.calls_by_role}")
    print(f"  tokens in/out      {run.usage.input_tokens}/{run.usage.output_tokens}")
    lock = runtime.repository.get_cycle_lock(run_id)
    print(f"  lock               {'held for cycle ' + str(lock.cycle) if lock else 'free'}")
    states = runtime.repository.get_all_current_arm_states(run_id)
    for arm in run.configuration.arms:
        state = states.get(arm.value)
        if state is not None:
            print(
                f"  {arm.value:<14} active={len(state.active_memories):>3} "
                f"tokens={state.active_tokens:>4}/{run.configuration.memory_budget_tokens}"
            )
    print(f"  interviews         {len(runtime.repository.get_interviews(run_id))}")
    print(f"  analysed cycles    {_analysed(runtime.repository, run_id, run.current_cycle)}")
    return 0


def _analysed(repository: DynamoRepository, run_id: str, through: int) -> list[int]:
    return [
        cycle
        for cycle in range(1, through + 1)
        if repository.get_cycle_analysis(run_id, cycle=cycle) is not None
    ]


# ----------------------------------------------------------------------- cycle


def _command_cycle(args: argparse.Namespace) -> int:
    """Advance the run by one cycle, in this process, against the deployed table.

    Separate from ``schedule invoke-once``, which asks the deployed function to do
    it. This one is for a smoke test, where seeing the traceback matters more than
    exercising the deployment path.
    """
    from attention_sink.aws.run_cycle import run_one_cycle

    runtime = _runtime()
    result = run_one_cycle(
        runtime,
        run_id=args.run_id or runtime.settings.run_id,
        cycle=args.cycle,
        invocation_id=uuid.uuid4().hex,
    )
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["result_code"] in {"committed", "already_committed"} else 1


# -------------------------------------------------------------------- schedule


def _command_schedule(args: argparse.Namespace) -> int:
    """Inspect, arm, disarm, or fire the schedule once."""
    settings = AwsSettings.from_env()
    name = f"attention-sink-{settings.environment.value}-cycle"
    client = boto3.Session().client("scheduler")

    if args.action == "inspect":
        return _describe_schedule(client, name)
    if args.action == "invoke-once":
        return _invoke_once(settings)

    wanted = "ENABLED" if args.action == "enable" else "DISABLED"
    if wanted == "ENABLED" and not settings.execution_enabled:
        print(
            "FAILED: the run-cycle function has AS_EXECUTION_ENABLED=false, so an "
            "enabled schedule would fire into a deployment that refuses to run. Arm "
            "the function first.",
            file=sys.stderr,
        )
        return 1
    current = client.get_schedule(Name=name)
    client.update_schedule(
        Name=name,
        State=wanted,
        ScheduleExpression=current["ScheduleExpression"],
        ScheduleExpressionTimezone=current.get("ScheduleExpressionTimezone", "UTC"),
        FlexibleTimeWindow=current["FlexibleTimeWindow"],
        Target=current["Target"],
    )
    print(f"{name}: {current['State']} -> {wanted}")
    return 0


def _describe_schedule(client: Any, name: str) -> int:
    schedule = client.get_schedule(Name=name)
    print(f"{name}")
    print(f"  state       {schedule['State']}")
    print(f"  expression  {schedule['ScheduleExpression']}")
    print(f"  target      {schedule['Target']['Arn'].rsplit(':', 1)[-1]}")
    print(f"  input       {schedule['Target'].get('Input', '')}")
    print(f"  dead letter {schedule['Target'].get('DeadLetterConfig', {}).get('Arn', 'none')}")
    return 0


def _invoke_once(settings: AwsSettings) -> int:
    """Fire the deployed function once, exactly as the schedule would."""
    function = os.environ.get("AS_RUN_CYCLE_FUNCTION", "").strip()
    if not function:
        print(
            "FAILED: set AS_RUN_CYCLE_FUNCTION to the deployed function name "
            "(the RunCycleFunctionName stack output)",
            file=sys.stderr,
        )
        return 1
    response = (
        boto3.Session()
        .client("lambda")
        .invoke(
            FunctionName=function,
            InvocationType="RequestResponse",
            Payload=json.dumps({"run_id": settings.run_id, "source": "operator"}).encode("utf-8"),
        )
    )
    payload = json.loads(response["Payload"].read())
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if response.get("FunctionError") is None else 1


# ------------------------------------------------------------------- execution


def _command_execution(args: argparse.Namespace) -> int:
    """Arm or disarm the deployed run-cycle function.

    The one switch that lets a deployment spend money on its own. It is flipped on a
    deployed function rather than baked into the stack, so that arming is an action
    somebody took at a moment that is recorded in CloudTrail, not a property of a
    template somebody merged.

    This is deliberately a different command from ``schedule enable``. Arming the
    function lets a cycle run when something asks for one; arming the schedule makes
    something ask, forever, on a timer. Requiring both, separately, is what stops one
    mistake from becoming a run.
    """
    function = os.environ.get("AS_RUN_CYCLE_FUNCTION", "").strip()
    if not function:
        print(
            "FAILED: set AS_RUN_CYCLE_FUNCTION to the deployed function name "
            "(the RunCycleFunctionName stack output)",
            file=sys.stderr,
        )
        return 1
    client = boto3.Session().client("lambda")
    current = client.get_function_configuration(FunctionName=function)
    variables = dict(current.get("Environment", {}).get("Variables", {}))

    if args.action == "inspect":
        print(f"{function}")
        print(f"  execution enabled  {variables.get('AS_EXECUTION_ENABLED')}")
        print(f"  bedrock allowed    {variables.get('ALLOW_BEDROCK_CALLS')}")
        print(f"  environment        {variables.get('AS_DEPLOYMENT_ENVIRONMENT')}")
        print(f"  run                {variables.get('AS_PILOT_RUN_ID')}")
        print(f"  cycle ceiling      {variables.get('AS_MAX_CYCLES', '(protocol maximum)')}")
        return 0

    wanted = "true" if args.action == "enable" else "false"
    was = variables.get("AS_EXECUTION_ENABLED", "false")
    variables["AS_EXECUTION_ENABLED"] = wanted
    client.update_function_configuration(
        FunctionName=function, Environment={"Variables": variables}
    )
    print(f"{function}: AS_EXECUTION_ENABLED {was} -> {wanted}")
    if wanted == "true":
        print("  the deployment can now advance the run. Disarm it when you are done.")
    return 0


# ---------------------------------------------------------------------- export


def _command_export(args: argparse.Namespace) -> int:
    """Write the complete dataset to the export bucket."""
    from attention_sink.analysis import export_dataset

    runtime = _runtime()
    run_id = args.run_id or runtime.settings.run_id
    run = runtime.repository.get_run(run_id)
    if run is None:
        print(f"no run {run_id}", file=sys.stderr)
        return 1
    bucket = runtime.settings.export_bucket
    if bucket is None:
        print("FAILED: AS_EXPORT_BUCKET is not set", file=sys.stderr)
        return 1

    export_id = args.export_id or f"export-{datetime.now(UTC).strftime('%Y%m%dT%H%M%S')}"
    storage = S3ExportStorage(
        bucket=bucket,
        run_id=run_id,
        export_id=export_id,
        client=runtime.s3(),
        run_kind=run.run_kind,
    )
    result = export_dataset(
        storage,
        run=run,
        repository=runtime.repository,
        bundle=runtime.bundle,
        analysis=runtime.analysis().analyse_run(run_id),
        export_id=export_id,
    )
    print(f"exported {len(result.files)} files plus checksums to {result.manifest.directory}")
    print(f"labels: {', '.join(result.manifest.labels)}")
    return 0


# ------------------------------------------------------------------------ main


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="aws_cli", description=__doc__)
    parser.add_argument("--run-id", default=None)
    subcommands = parser.add_subparsers(dest="command", required=True)

    for name, handler in (
        ("preflight", _command_preflight),
        ("status", _command_status),
    ):
        subcommands.add_parser(name).set_defaults(handler=handler)

    bootstrap = subcommands.add_parser("bootstrap")
    bootstrap.add_argument(
        "--interview",
        action="store_true",
        # The cycle-0 interview is the baseline every identity-drift score is measured
        # against, so a run without one can be advanced but not fully analysed. It is
        # six interviewer calls, so creating a run stays free and taking the baseline
        # is a separate, deliberate act.
        help="take the cycle-0 baseline interview; costs six model calls",
    )
    bootstrap.set_defaults(handler=_command_bootstrap)

    cycle = subcommands.add_parser("cycle")
    cycle.add_argument("--cycle", type=int, default=None)
    cycle.set_defaults(handler=_command_cycle)

    execution = subcommands.add_parser("execution")
    execution.add_argument("action", choices=("inspect", "enable", "disable"))
    execution.set_defaults(handler=_command_execution)

    schedule = subcommands.add_parser("schedule")
    schedule.add_argument("action", choices=("inspect", "enable", "disable", "invoke-once"))
    schedule.set_defaults(handler=_command_schedule)

    export = subcommands.add_parser("export")
    export.add_argument("--export-id", default=None)
    export.set_defaults(handler=_command_export)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run one operator command.

    Returns:
        A process exit status. Expected refusals return 1 with a message on stderr
        rather than a traceback.
    """
    args = _parser().parse_args(argv)
    try:
        status: int = args.handler(args)
    except ConfigurationError as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        return 1
    return status


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
