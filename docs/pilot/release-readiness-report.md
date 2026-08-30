# Release readiness — Attention Sink pilot

## Decision

**READY.**

The canonical experiment is complete, verified, exported and public, and every one of
the sixteen conditions holds against the live deployment. Nothing is PARTIAL. The
scheduler is disabled and the run-cycle function is disarmed, so the deployment makes
no model calls and costs only storage.

## What was produced

|                  |                                                                                                                        |
| ---------------- | ---------------------------------------------------------------------------------------------------------------------- |
| Canonical run    | `run_aws_canonical`, kind `AWS_CANONICAL`, status `completed`, cycle 24/24                                             |
| Protocol         | `pilot-v1`, status FROZEN                                                                                              |
| Manifest         | `sha256:8e218e488c8745f427a1babd216e674da7a019860b799e5d60356a64eaa0971f`                                              |
| Budget           | 208 tokens, source `bedrock_converse_usage`                                                                            |
| Models           | `amazon.nova-micro-v1:0` (writer, summary, interview, evaluator, counter), `amazon.titan-embed-text-v2:0` (embeddings) |
| Region / account | `us-east-1` / `****2684`                                                                                               |
| Exhibition       | https://d1qskxceo899me.cloudfront.net                                                                                  |
| Read API         | https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com                                                                 |
| Dataset          | `s3://attentionsink-production-exportbucket4e99310e-gyohrcdchg5s/canonical/run_aws_canonical/`, eighteen files         |

## The sixteen conditions

| #   | Condition                                       | State | How it was established                                                                   |
| --- | ----------------------------------------------- | ----- | ---------------------------------------------------------------------------------------- |
| 1   | All six policy invariants pass                  | PASS  | `verify_run.py`, twenty-six checks, on both the canonical and local runs                 |
| 2   | Local mode still passes                         | PASS  | `make local-all` from an empty database: 24 cycles, 26 checks                            |
| 3   | A real six-arm cycle completes                  | PASS  | Every one of the 24 cycles wrote six snapshots from six model calls                      |
| 4   | A canonical scheduler-triggered cycle completes | PASS  | 95 run-cycle invocations, 24 committed cycles; the rest refused cleanly                  |
| 5   | All twenty-four canonical cycles complete       | PASS  | 144 snapshots, 24 analysed cycles, 18 interviews                                         |
| 6   | Duplicate execution creates no duplicate        | PASS  | Cycle lock plus `attribute_not_exists`; "no duplicate snapshot"                          |
| 7   | Six-arm commits remain atomic                   | PASS  | `TransactWriteItems`; no cycle holds a partial set                                       |
| 8   | The final protocol is immutable                 | PASS  | 20 freeze tests; `make pilot-freeze` now refuses to rewrite the manifest                 |
| 9   | All four metrics expose evidence                | PASS  | "metric evidence resolves"; 2,062 rows, every link resolvable                            |
| 10  | Dataset checksums pass                          | PASS  | `shasum -a 256 -c checksums.sha256`: 17 of 17 OK, outside this repo                      |
| 11  | The public frontend contains no fixture data    | PASS  | `VITE_FIXTURE_MODE=false`; no simulated banner on the deployed site                      |
| 12  | Public S3 access is blocked                     | PASS  | Direct object GET returns 403; no bucket policy is public                                |
| 13  | Public API has no mutation route                | PASS  | POST, PUT, PATCH and DELETE all 404; the read role holds no write action                 |
| 14  | Scheduler is disabled after completion          | PASS  | `preflight`: schedule DISABLED, `AS_EXECUTION_ENABLED` false                             |
| 15  | Call limits are enabled                         | PASS  | 6 writers/cycle, 600/run; 316 cycle calls spent                                          |
| 16  | Methodology describes application-level memory  | PASS  | "not an internal KV cache", "not evidence about a production model", "are not conscious" |

## Test results

| Suite                            | Result                          |
| -------------------------------- | ------------------------------- |
| Python, property and integration | 1,159 passed, 11 skipped        |
| Coverage gates (per package)     | 97–100%, all above their floors |
| TypeScript unit (web)            | 11 passed                       |
| CDK assertions                   | 48 passed                       |
| CDK synth                        | all three environments          |
| Playwright, local fixture stack  | 66 passed, 2 skipped            |
| Playwright, deployed CloudFront  | 66 passed, 2 skipped            |
| `verify_run.py`, canonical run   | 26 of 26 checks                 |
| `verify_run.py`, local run       | 26 of 26 checks                 |
| Dataset checksums                | 17 of 17 files OK               |

## Known limitations

**Scientific.** One model, one inference setting, one repetition, one seed world.
Six mechanisms diverged; that they diverged _this way_ is a single observation, not an
effect size. The experiment is about explicit external memory records and makes no
claim about the model's internal KV cache, attention, hidden state or awareness.

**Operational.** The read API is capped at 100 concurrent executions. That is a
runaway guard, not a capacity plan: an exhibition that went viral would meet it, and
a visitor who does gets the client's stated error rather than a broken page. The cap
is one line in `pilot-stack.ts`. The distribution serves only the canonical export
prefix, so a staging rehearsal is never reachable as though it were a result.

**Cost.** The estimate in the usage report is measured counts times configured rates.
It is not a bill and nothing here guarantees a zero-cost account.

## Remaining blockers

None. The deploy carrying the concurrency cap and the dataset behaviour is applied;
its `s3:GetObject` grant to the CloudFront service principal is scoped by
`AWS:SourceArn` to this distribution alone, and the export bucket remains private to
everything else.
