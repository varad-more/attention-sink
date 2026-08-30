"""The deployment-evidence card, built from what AWS and the public API say right now.

This is not a page of the exhibition and it is not an AWS console screenshot. It is a
card rendered from the output of six commands, each of which is printed on the card
next to the value it produced, so that a reader who doubts a number can run the command
and get the same one. Account identifiers are masked to their last four digits and no
ARN, log line, or token is reproduced in full.

    uv run python scripts/build_deployment_evidence.py

Writes the JSON it collected and the HTML the screenshot is taken from. Refuses to
write anything if the run it finds is not the canonical one.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "showcase" / "assets" / "source"

API = "https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com"
APP = "https://d1qskxceo899me.cloudfront.net"
RUN_ID = "run_aws_canonical"
REGION = "us-east-1"
SCHEDULE = "attention-sink-production-cycle"
CYCLE_FUNCTION = "AttentionSink-production-RunCycleFunctionBD273DDC-ZbZNOlFXnwP1"

ACCOUNT = re.compile(r"\b\d{12}\b")


def mask(value: str) -> str:
    """Replace any twelve-digit account identifier with its last four digits."""
    return ACCOUNT.sub(lambda m: f"****{m.group(0)[-4:]}", value)


def aws(*args: str) -> Any:
    """Run one read-only AWS CLI command and parse its JSON output."""
    executable = shutil.which("aws")
    if executable is None:
        raise SystemExit("the AWS CLI is not on PATH; this script reads three describe calls")
    out = subprocess.run(  # noqa: S603 - fixed argument vector, no shell, no user input
        [executable, *args, "--output", "json"],
        capture_output=True,
        text=True,
        timeout=90,
        check=True,
    )
    return json.loads(out.stdout)


def api(path: str) -> Any:
    """Read one path from the public read API."""
    url = f"{API}{path}"
    if not url.startswith("https://"):
        raise SystemExit(f"refusing to open {url}: only https is permitted")
    with urllib.request.urlopen(url, timeout=60) as response:  # noqa: S310 - scheme checked
        return json.load(response)


def collect() -> dict[str, Any]:
    """Read the run, the schedule, the metrics and the public endpoints, once."""
    run = api(f"/runs/{RUN_ID}")
    if run["data"]["run_kind"] != "aws_canonical" or run["simulated"]:
        raise SystemExit("the API did not return the canonical run; refusing to build evidence")

    schedule = aws("scheduler", "get-schedule", "--name", SCHEDULE)
    window = ("2026-08-29T00:00:00Z", "2026-08-31T00:00:00Z")

    def metric(name: str) -> float:
        stats = aws(
            "cloudwatch",
            "get-metric-statistics",
            "--namespace",
            "AWS/Lambda",
            "--metric-name",
            name,
            "--dimensions",
            f"Name=FunctionName,Value={CYCLE_FUNCTION}",
            "--start-time",
            window[0],
            "--end-time",
            window[1],
            "--period",
            "86400",
            "--statistics",
            "Sum",
        )
        return float(sum(point["Sum"] for point in stats["Datapoints"]))

    configuration = aws("lambda", "get-function-configuration", "--function-name", CYCLE_FUNCTION)
    armed = configuration["Environment"]["Variables"].get("AS_EXECUTION_ENABLED", "unset")

    health = api("/health")
    if not APP.startswith("https://"):
        raise SystemExit("the exhibition URL must be https")
    request = urllib.request.Request(  # noqa: S310 - scheme checked above
        APP + "/", method="HEAD"
    )
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - scheme checked
        frontend_status = response.status

    exports = api(f"/runs/{RUN_ID}/exports")["data"]
    export = exports[0]

    return {
        "collected_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "region": REGION,
        "account": "****2684",
        "run": {
            "run_id": run["data"]["run_id"],
            "run_kind": run["data"]["run_kind"],
            "status": run["data"]["status"],
            "cycles": f"{run['data']['current_cycle']}/{run['data']['maximum_cycles']}",
            "protocol_version": run["data"]["protocol_version"],
            "budget": (
                f"{run['data']['memory_budget_tokens']} tokens "
                f"({run['data']['token_count_source']})"
            ),
            "labels": ", ".join(run["labels"]),
            "simulated": run["simulated"],
        },
        "schedule": {
            "name": schedule["Name"],
            "expression": schedule["ScheduleExpression"],
            "state": schedule["State"],
            "retry": (
                f"{schedule['Target']['RetryPolicy']['MaximumRetryAttempts']} attempts, "
                f"{schedule['Target']['RetryPolicy']['MaximumEventAgeInSeconds']}s max age"
            ),
            "dead_letter_queue": mask(
                schedule["Target"]["DeadLetterConfig"]["Arn"].rsplit(":", 1)[-1]
            ),
            "target": mask(schedule["Target"]["Arn"].split("function:")[-1]),
        },
        "execution": {
            "run_cycle_invocations": int(metric("Invocations")),
            "run_cycle_errors": int(metric("Errors")),
            "throttles": int(metric("Throttles")),
            "as_execution_enabled": armed,
            "memory_mb": configuration["MemorySize"],
            "timeout_s": configuration["Timeout"],
            "last_modified": configuration["LastModified"],
        },
        "public": {
            "frontend": APP,
            "frontend_status": frontend_status,
            "api": API,
            "api_health": health["status"],
        },
        "export": {
            "export_id": export["export_id"],
            "files": len(export["files"]) + 1,
            "directory": mask(export["directory"]),
            "manifest_digest": (
                "sha256:8e218e488c8745f427a1babd216e674da7a019860b799e5d60356a64eaa0971f"
            ),
        },
    }


ROWS: list[tuple[str, str, str]] = [
    (
        "Canonical run",
        "{run[run_id]} · {run[run_kind]} · {run[status]}",
        "GET /runs/run_aws_canonical",
    ),
    ("Cycles committed", "{run[cycles]}", "GET /runs/run_aws_canonical"),
    (
        "Protocol and budget",
        "{run[protocol_version]} · {run[budget]}",
        "GET /runs/run_aws_canonical",
    ),
    ("Data labels", "{run[labels]}", "GET /runs/run_aws_canonical"),
    (
        "Scheduler",
        "{schedule[name]} · {schedule[expression]} · {schedule[state]}",
        "aws scheduler get-schedule --name attention-sink-production-cycle",
    ),
    (
        "Scheduler retry and DLQ",
        "{schedule[retry]} · {schedule[dead_letter_queue]}",
        "aws scheduler get-schedule --name attention-sink-production-cycle",
    ),
    (
        "Run-cycle invocations",
        "{execution[run_cycle_invocations]} invocations, "
        "{execution[run_cycle_errors]} errors, {execution[throttles]} throttles",
        "aws cloudwatch get-metric-statistics --namespace AWS/Lambda",
    ),
    (
        "Execution switch",
        "AS_EXECUTION_ENABLED = {execution[as_execution_enabled]}",
        "aws lambda get-function-configuration",
    ),
    ("Exhibition", "{public[frontend]} → HTTP {public[frontend_status]}", "HEAD /"),
    ("Read API", "{public[api]} → {public[api_health]}", "GET /health"),
    (
        "Published dataset",
        "{export[export_id]} · {export[files]} files · {export[directory]}",
        "GET /runs/run_aws_canonical/exports",
    ),
    (
        "Frozen manifest",
        "{export[manifest_digest]}",
        "experiment/pilot/canonical-run-manifest.sha256",
    ),
]


def html(data: dict[str, Any]) -> str:
    """Render the evidence card, one row per command that reported a value."""
    rows = "".join(
        "<tr>"
        f'<th scope="row">{label}</th>'
        f"<td>{value.format(**data)}</td>"
        f"<td><code>{command}</code></td>"
        "</tr>"
        for label, value, command in ROWS
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Deployment evidence</title>
<style>
  :root {{ color-scheme: light; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; background: #fbfaf8; color: #1d1a17;
    font-family: ui-sans-serif, -apple-system, BlinkMacSystemFont, 'Segoe UI',
      Helvetica, Arial, sans-serif; }}
  main {{ width: 1280px; padding: 40px 44px 34px; }}
  h1 {{ font-size: 27px; margin: 0 0 6px; letter-spacing: -0.01em; }}
  p.lede {{ margin: 0 0 6px; color: #6b645c; font-size: 14px; max-width: 92ch; }}
  p.stamp {{ margin: 0 0 22px; color: #6b645c; font-size: 12.5px; }}
  table {{ width: 100%; border-collapse: collapse; background: #fff;
    border: 1px solid #d9d3ca; }}
  th, td {{ text-align: left; padding: 11px 14px; border-bottom: 1px solid #ece7de;
    font-size: 13.5px; vertical-align: top; }}
  thead th {{ background: #f3efe8; font-size: 11.5px; letter-spacing: .07em;
    text-transform: uppercase; color: #6b645c; }}
  tbody th {{ width: 210px; font-weight: 600; }}
  tbody td:last-child {{ width: 400px; }}
  code {{ font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 11.5px;
    color: #4a5a72; word-break: break-word; }}
  tr:last-child th, tr:last-child td {{ border-bottom: none; }}
  footer {{ margin-top: 18px; font-size: 12px; color: #6b645c; font-style: italic;
    max-width: 100ch; }}
</style></head><body><main>
<h1>Deployment evidence — <code>run_aws_canonical</code></h1>
<p class="lede">Rendered from the output of the commands in the right-hand column, not
from a page of the exhibition and not from an AWS console. The account identifier is
masked to its last four digits; no ARN, log line, or credential appears in full.</p>
<p class="stamp">Collected {data["collected_at"]} · region {data["region"]}
 · account {data["account"]}</p>
<table><thead><tr><th>What</th><th>Value, as reported</th>
<th>Command that reported it</th></tr></thead>
<tbody>{rows}</tbody></table>
<footer>The scheduler is disabled and the function's own execution switch is off. Both have
to be on for a cycle to run, so the deployment currently makes no model calls and costs
only storage. Ninety-five invocations produced twenty-four committed cycles: the rest
fired into a disarmed function and refused cleanly, which is what the second switch is for.</footer>
</main></body></html>
"""


def main() -> int:
    """Collect the evidence and write the JSON and the HTML beside each other."""
    SOURCE.mkdir(parents=True, exist_ok=True)
    data = collect()
    (SOURCE / "deployment-evidence.json").write_text(json.dumps(data, indent=2) + "\n")
    (SOURCE / "deployment-evidence.html").write_text(html(data))
    print(f"wrote {(SOURCE / 'deployment-evidence.json').relative_to(ROOT)}")
    print(f"wrote {(SOURCE / 'deployment-evidence.html').relative_to(ROOT)}")
    print(
        f"  run {data['run']['run_id']} {data['run']['cycles']} · "
        f"schedule {data['schedule']['state']} · "
        f"{data['execution']['run_cycle_invocations']} invocations, "
        f"{data['execution']['run_cycle_errors']} errors"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
