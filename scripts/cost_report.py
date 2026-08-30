#!/usr/bin/env python
"""Report what a deployed run actually spent, and estimate what that costs.

Every count is measured: the model calls come from the run's own ledger, the Lambda
invocations and CloudFront bytes from CloudWatch, the record count from the table, the
stored bytes from the buckets. Only the prices are configuration, and they are
configuration precisely because they are the part this repository cannot know -- they
change, they differ by account, and a number printed here is an estimate rather than
a bill.

    AS_TABLE_NAME=... python scripts/cost_report.py --run-id run_aws_canonical
    python scripts/cost_report.py --prices prices.json      # override any rate

Writes `docs/pilot/aws-cost-and-usage-report.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import boto3

from attention_sink.aws.dynamodb import DynamoRepository
from attention_sink.pilot.repositories import RunRecord

REPORT = Path("docs/pilot/aws-cost-and-usage-report.md")

DEFAULT_PRICES: dict[str, float] = {
    "nova_micro_input_per_1k": 0.000035,
    "nova_micro_output_per_1k": 0.00014,
    "lambda_per_gb_second": 0.0000166667,
    "lambda_per_request": 0.0000002,
    "dynamodb_storage_per_gb_month": 0.25,
    "s3_storage_per_gb_month": 0.023,
    "cloudfront_per_gb": 0.085,
    "cloudfront_per_10k_requests": 0.0075,
}
"""Published on-demand US East rates at the time of writing, in US dollars.

Not authoritative and not a quote. Every one is overridable with `--prices`, and the
report says on its face that these are the numbers it was given rather than the
numbers anybody was charged.
"""

ANALYSIS_ROLES = frozenset({"evaluator", "embedding"})


@dataclass(frozen=True, slots=True)
class Observed:
    """What the deployment's own telemetry says happened."""

    lambda_invocations: dict[str, int]
    lambda_gb_seconds: dict[str, float]
    cloudfront_requests: int
    cloudfront_bytes: int
    table_items: int
    table_bytes: int
    bucket_bytes: dict[str, int]
    schedule_state: str


def stack_outputs(stack: str) -> dict[str, str]:
    """Every output of a deployed stack, by key."""
    client = boto3.client("cloudformation")
    described = client.describe_stacks(StackName=stack)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in described.get("Outputs", [])}


def metric_total(
    namespace: str, name: str, dimensions: dict[str, str], *, since: datetime, statistic: str
) -> float:
    """One CloudWatch metric, summed over the window."""
    client = boto3.client("cloudwatch")
    response = client.get_metric_statistics(
        Namespace=namespace,
        MetricName=name,
        Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
        StartTime=since,
        EndTime=datetime.now(UTC),
        Period=86_400,
        Statistics=[statistic],
    )
    return float(sum(point[statistic] for point in response["Datapoints"]))


def bucket_bytes(bucket: str) -> int:
    """Stored bytes, summed over the objects themselves.

    Not from `BucketSizeBytes`: that metric is published once a day, so a bucket
    written to this morning reports zero, and a cost report whose storage line is
    silently a day stale is worse than one that costs a few list calls.
    """
    paginator = boto3.client("s3").get_paginator("list_objects_v2")
    return sum(
        int(item["Size"])
        for page in paginator.paginate(Bucket=bucket)
        for item in page.get("Contents", [])
    )


def observe(outputs: dict[str, str], *, since: datetime) -> Observed:
    """Read every counter the deployment publishes about itself."""
    functions = {
        "run-cycle": outputs["RunCycleFunctionName"],
        "analysis": outputs["AnalysisFunctionName"],
        "read-api": outputs["ReadApiFunctionName"],
    }
    invocations = {
        label: int(
            metric_total(
                "AWS/Lambda", "Invocations", {"FunctionName": name}, since=since, statistic="Sum"
            )
        )
        for label, name in functions.items()
    }
    durations = {
        label: metric_total(
            "AWS/Lambda", "Duration", {"FunctionName": name}, since=since, statistic="Sum"
        )
        for label, name in functions.items()
    }
    client = boto3.client("lambda")
    memory = {
        label: client.get_function_configuration(FunctionName=name)["MemorySize"]
        for label, name in functions.items()
    }
    gb_seconds = {
        label: (durations[label] / 1000.0) * (memory[label] / 1024.0) for label in functions
    }

    distribution = outputs["CloudFrontUrl"].removeprefix("https://").split(".")[0]
    identifier = _distribution_id(distribution)
    cloudfront_requests = int(
        metric_total(
            "AWS/CloudFront",
            "Requests",
            {"DistributionId": identifier, "Region": "Global"},
            since=since,
            statistic="Sum",
        )
    )
    cloudfront_bytes = int(
        metric_total(
            "AWS/CloudFront",
            "BytesDownloaded",
            {"DistributionId": identifier, "Region": "Global"},
            since=since,
            statistic="Sum",
        )
    )

    table = boto3.client("dynamodb").describe_table(TableName=outputs["TableName"])["Table"]
    schedule = boto3.client("scheduler").get_schedule(Name=outputs["ScheduleName"])
    # ItemCount and TableSizeBytes are refreshed about every six hours, so both are
    # an estimate and both can read zero for a table filled this morning. The run's
    # own record count, below, is exact.
    return Observed(
        lambda_invocations=invocations,
        lambda_gb_seconds=gb_seconds,
        cloudfront_requests=cloudfront_requests,
        cloudfront_bytes=cloudfront_bytes,
        table_items=int(table["ItemCount"]),
        table_bytes=int(table["TableSizeBytes"]),
        bucket_bytes={
            "export": bucket_bytes(outputs["ExportBucketName"]),
            "frontend": bucket_bytes(outputs["FrontendBucketName"]),
        },
        schedule_state=str(schedule["State"]),
    )


def _distribution_id(domain_prefix: str) -> str:
    """Resolve a distribution's id from its domain name.

    Raises:
        SystemExit: No distribution in this account serves that domain.
    """
    paginator = boto3.client("cloudfront").get_paginator("list_distributions")
    for page in paginator.paginate():
        for item in page["DistributionList"].get("Items", []):
            if item["DomainName"].startswith(domain_prefix):
                return str(item["Id"])
    msg = f"no CloudFront distribution serves {domain_prefix}"
    raise SystemExit(msg)


def markdown_table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    """Render a column-aligned Markdown table, the way Prettier would format it."""
    if not rows:
        rows = [tuple("-" for _ in header)]
    widths = [max(len(cell) for cell in column) for column in zip(header, *rows, strict=True)]
    return "\n".join(
        "| " + " | ".join(cell.ljust(width) for cell, width in zip(row, widths, strict=True)) + " |"
        for row in (header, tuple("-" * width for width in widths), *rows)
    )


def per_cycle(run: RunRecord) -> list[tuple[str, ...]]:
    """Model calls attributed to each cycle, from the run's ledger.

    Analysis gets a column of its own rather than disappearing into a total. The judge
    and the embedding model are asked about the run rather than about a cycle, and the
    ledger records them at cycle zero; folded into that row's total they turned six
    interviews into eleven hundred calls with nothing on the row to explain it.
    """
    counts: dict[int, Counter[str]] = {}
    for entry in run.usage.ledger:
        counts.setdefault(entry.cycle, Counter())[entry.operation] += 1
    rows: list[tuple[str, ...]] = []
    for cycle in sorted(counts):
        tally = counts[cycle]
        analysis = tally.get("evaluator", 0) + tally.get("embedding", 0)
        rows.append(
            (
                str(cycle),
                str(tally.get("writer", 0)),
                str(tally.get("token_counter", 0)),
                str(tally.get("summarizer", 0)),
                str(tally.get("interviewer", 0)),
                str(analysis),
                str(sum(tally.values())),
            )
        )
    return rows


def per_arm(run: RunRecord) -> list[tuple[str, ...]]:
    """Model calls attributed to each arm, from the run's ledger."""
    counts: dict[str, Counter[str]] = {}
    for entry in run.usage.ledger:
        if entry.arm_id is None:
            continue
        counts.setdefault(entry.arm_id, Counter())[entry.operation] += 1
    return [
        (
            arm,
            str(counts[arm].get("writer", 0)),
            str(counts[arm].get("token_counter", 0)),
            str(counts[arm].get("summarizer", 0)),
            str(counts[arm].get("interviewer", 0)),
            str(sum(counts[arm].values())),
        )
        for arm in sorted(counts)
    ]


def money(value: float) -> str:
    """Render a dollar amount, never rounding a real cost to nothing."""
    return f"${value:.4f}" if value < 1 else f"${value:.2f}"


def count_records(repository: DynamoRepository, run: RunRecord) -> dict[str, int]:
    """Exactly what this run stored, read back rather than estimated.

    Queried per arm rather than scanned: a scan would count the whole table, which is
    both more expensive and the wrong number when a table holds more than one run.
    """
    snapshots = sum(
        len(repository.list_arm_snapshots(run.run_id, arm_id=arm)) for arm in run.configuration.arms
    )
    return {
        "snapshots": snapshots,
        "interviews": len(repository.get_interviews(run.run_id)),
        "metrics": len(repository.get_metrics(run.run_id)),
    }


def write_report(
    *,
    run: RunRecord,
    observed: Observed,
    counted: dict[str, int],
    prices: dict[str, float],
    since: datetime,
) -> Path:
    """Write the cost and usage report."""
    usage = run.usage
    roles = usage.calls_by_role
    records = counted
    summariser = roles.get("summarizer", 0)
    writers = roles.get("writer", 0)

    text_input_cost = usage.input_tokens / 1000 * prices["nova_micro_input_per_1k"]
    text_output_cost = usage.output_tokens / 1000 * prices["nova_micro_output_per_1k"]
    lambda_cost = (
        sum(observed.lambda_gb_seconds.values()) * prices["lambda_per_gb_second"]
        + sum(observed.lambda_invocations.values()) * prices["lambda_per_request"]
    )
    cloudfront_cost = (
        observed.cloudfront_bytes / 1_000_000_000 * prices["cloudfront_per_gb"]
        + observed.cloudfront_requests / 10_000 * prices["cloudfront_per_10k_requests"]
    )
    s3_gb = sum(observed.bucket_bytes.values()) / 1_000_000_000
    table_gb = observed.table_bytes / 1_000_000_000
    storage_cost = (
        s3_gb * prices["s3_storage_per_gb_month"]
        + table_gb * prices["dynamodb_storage_per_gb_month"]
    )
    total = text_input_cost + text_output_cost + lambda_cost + cloudfront_cost + storage_cost

    calls = markdown_table(
        ("role", "calls", "what makes them"),
        [
            ("writer", str(writers), "one per arm per cycle"),
            (
                "token counter",
                str(roles.get("token_counter", 0)),
                "one per arm per cycle (ADR-013)",
            ),
            ("summarizer", str(summariser), "the Dreamer arm, only when it compresses"),
            ("interviewer", str(roles.get("interviewer", 0)), "six per checkpoint"),
            ("evaluator", str(roles.get("evaluator", 0)), "the judge, in analysis"),
            ("embedding", str(roles.get("embedding", 0)), "identity vectors, in analysis"),
            ("**total**", f"**{usage.total_calls}**", ""),
        ],
    )
    tokens = markdown_table(
        ("quantity", "value"),
        [
            ("input tokens", f"{usage.input_tokens:,}"),
            ("output tokens", f"{usage.output_tokens:,}"),
            ("failed calls", str(usage.failed_calls)),
            ("retries", str(usage.retries)),
            ("simulated calls", str(usage.simulated_calls)),
        ],
    )
    cycles = markdown_table(
        ("cycle", "writer", "counts", "summary", "interview", "analysis", "total"),
        per_cycle(run),
    )
    arms = markdown_table(
        ("arm", "writer", "counts", "summary", "interview", "total"), per_arm(run)
    )
    infrastructure = markdown_table(
        ("resource", "measured"),
        [
            ("run-cycle invocations", str(observed.lambda_invocations["run-cycle"])),
            ("analysis invocations", str(observed.lambda_invocations["analysis"])),
            ("read-API invocations", str(observed.lambda_invocations["read-api"])),
            ("Lambda GB-seconds", f"{sum(observed.lambda_gb_seconds.values()):.1f}"),
            ("DynamoDB items (table estimate)", f"{observed.table_items:,}"),
            ("DynamoDB bytes (table estimate)", f"{observed.table_bytes:,}"),
            ("snapshots stored", f"{records['snapshots']:,}"),
            ("interviews stored", f"{records['interviews']:,}"),
            ("metric rows stored", f"{records['metrics']:,}"),
            ("export bucket bytes", f"{observed.bucket_bytes['export']:,}"),
            ("frontend bucket bytes", f"{observed.bucket_bytes['frontend']:,}"),
            ("CloudFront requests", f"{observed.cloudfront_requests:,}"),
            ("CloudFront bytes", f"{observed.cloudfront_bytes:,}"),
            ("schedule state", observed.schedule_state),
        ],
    )
    estimate = markdown_table(
        ("line", "basis", "estimate"),
        [
            ("Bedrock input", f"{usage.input_tokens:,} tokens", money(text_input_cost)),
            ("Bedrock output", f"{usage.output_tokens:,} tokens", money(text_output_cost)),
            (
                "Lambda",
                f"{sum(observed.lambda_gb_seconds.values()):.1f} GB-s",
                money(lambda_cost),
            ),
            (
                "CloudFront",
                f"{observed.cloudfront_bytes:,} bytes",
                money(cloudfront_cost),
            ),
            (
                "Storage (monthly)",
                f"{s3_gb:.6f} GB in S3, {table_gb:.6f} GB in DynamoDB",
                money(storage_cost),
            ),
            ("**total**", "", f"**{money(total)}**"),
        ],
    )
    rates = markdown_table(
        ("rate", "value"), [(f"`{k}`", f"{v}") for k, v in sorted(prices.items())]
    )

    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(
        f"""# AWS cost and usage — `{run.run_id}`

Generated by `scripts/cost_report.py`. Do not edit by hand; re-run the script.

**This is an estimate, not a bill.** Every count below is measured — from the run's own
call ledger, from CloudWatch, and from the resources themselves — and every _price_ is
configuration. The rates are published on-demand figures for this Region at the time of
writing, they are overridable with `--prices`, and nobody should treat the total as
what the account was charged. Consult Cost Explorer for that.

**Run:** `{run.run_id}` ({run.run_kind.value}) &nbsp;&nbsp; **Status:** {run.status.value}
&nbsp;&nbsp; **Cycle:** {run.current_cycle}/{run.configuration.maximum_cycles}
&nbsp;&nbsp; **Window:** since `{since.isoformat(timespec="seconds")}`

**Models:** `{run.configuration.writer_model.model_id}` (writer, summarizer,
interviewer, evaluator, token counter) and
`{run.configuration.embedding_model.model_id}` (embeddings), in
`{run.configuration.writer_model.region}`.

## Model calls

{calls}

{tokens}

### Dreamer overhead

The summarising arm is the only one that makes a second generation in a cycle, and it
makes one only when its mechanism decides to compress. It made **{summariser}**
summary calls against **{writers}** writer calls across the whole run — the price of
the sixth arm, in calls, is {summariser / writers * 100:.1f}% on top of the writing
every arm does.

## Per cycle

Cycle work is the first four columns. The judge and the embedding model are asked about
the run rather than about any one cycle, and the ledger records them at cycle zero;
they are the `analysis` column, and they are the reason that row is large.

{cycles}

## Per arm

Analysis calls are attributed to no arm: the judge and the embedding model are asked
about the run, not on behalf of one mechanism.

{arms}

## Infrastructure

{infrastructure}

## Estimated cost

{estimate}

### The rates this used

{rates}

## Scheduler

Final state: **{observed.schedule_state}**. The run-cycle function's own
`AS_EXECUTION_ENABLED` is the second switch, and both have to be on for a cycle to
happen. With the schedule disabled the deployment makes no model calls at all and
costs only storage.

## What is not counted here

- **Analysis calls made before the ledger recorded them.** The analysis service began
  folding its own evaluator and embedding calls into the run's usage partway through
  this run. Earlier passes are absent from the ledger above; the CloudWatch invocation
  counts include them.
- **The staging deployment**, which is a separate stack with its own resources.
- **Anything outside these stacks.** No VPC, NAT gateway, KMS key, or Route 53 record
  is created by this project.
""",
        encoding="utf-8",
    )
    return REPORT


def main() -> int:
    """Read the run and the deployment, and write the report."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-id", default="run_aws_canonical")
    parser.add_argument("--stack", default="AttentionSink-production")
    parser.add_argument("--prices", type=Path, default=None)
    parser.add_argument("--days", type=int, default=7, help="window to read CloudWatch over")
    args = parser.parse_args()

    prices = dict(DEFAULT_PRICES)
    if args.prices is not None:
        loaded: dict[str, Any] = json.loads(args.prices.read_text(encoding="utf-8"))
        unknown = sorted(set(loaded) - set(DEFAULT_PRICES))
        if unknown:
            print(f"unknown rates: {', '.join(unknown)}", file=sys.stderr)
            return 2
        prices.update({key: float(value) for key, value in loaded.items()})

    outputs = stack_outputs(args.stack)
    table = os.environ.get("AS_TABLE_NAME", "").strip() or outputs["TableName"]
    repository = DynamoRepository(table_name=table, client=boto3.client("dynamodb"))
    run = repository.get_run(args.run_id)
    if run is None:
        print(f"no run {args.run_id} in {table}", file=sys.stderr)
        return 1

    since = datetime.now(UTC) - timedelta(days=args.days)
    path = write_report(
        run=run,
        observed=observe(outputs, since=since),
        counted=count_records(repository, run),
        prices=prices,
        since=since,
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
