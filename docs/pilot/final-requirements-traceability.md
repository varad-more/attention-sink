# Final requirements traceability

Every requirement the pilot was given, and where the evidence that it holds actually
lives. One row per requirement, four possible states, and no row whose evidence is
"it looked right".

**PASS** — verified by something that would fail if it stopped holding.
**PARTIAL** — holds under a stated limit; the limit is named in the row.
**FAIL** — does not hold.
**BLOCKED** — cannot be established here, and why.

Run under test: `run_aws_canonical`, `AWS_CANONICAL`, 24/24 cycles, status `completed`,
protocol `pilot-v1` FROZEN, manifest
`sha256:8e218e488c8745f427a1babd216e674da7a019860b799e5d60356a64eaa0971f`,
`amazon.nova-micro-v1:0` and `amazon.titan-embed-text-v2:0` in `us-east-1`, budget 208
`bedrock_converse_usage` tokens.

Two rows below are PARTIAL, both for the same reason and neither for a reason found in
the code: the fix is written, tested, and committed, and applying it to the live
deployment needs one `cdk deploy` that grants CloudFront read access to the export
bucket. That grant is an IAM change, and it is the operator's to approve. Neither row
is one of the sixteen conditions on the release decision.

## The six policy invariants

| #   | Invariant                                            | State | Evidence                                                                        |
| --- | ---------------------------------------------------- | ----- | ------------------------------------------------------------------------------- |
| 1   | Every arm ends a cycle inside the budget             | PASS  | `verify_run.py` "no budget violations", both runs                               |
| 2   | Only the mechanism differs between arms              | PASS  | `test_pilot_blindness.py`; one shared stimulus per cycle, verified per cycle    |
| 3   | The pinned memory never leaves `arm_sink`            | PASS  | `verify_run.py` "pinned memory survives"                                        |
| 4   | A retired memory never returns                       | PASS  | `verify_run.py` "no forgotten memory returned"                                  |
| 5   | The stochastic arm replays from its recorded seed    | PASS  | `verify_run.py` "random provenance replays"; property tests in `tests/property` |
| 6   | A summary names at least two parents, all resolvable | PASS  | `verify_run.py` "dreamer lineage resolves", "compression records are distinct"  |

## The canonical run

| Requirement                                             | State | Evidence                                                             |
| ------------------------------------------------------- | ----- | -------------------------------------------------------------------- |
| Twenty-four cycles complete                             | PASS  | `aws-verify` at 24/24; 144 snapshots, six arms × twenty-four cycles  |
| One shared stimulus per cycle, across all six arms      | PASS  | `verify_run.py` "one stimulus per cycle", "arms share every cycle"   |
| Current arm states match their snapshots                | PASS  | `verify_run.py` "seed states match", "no completed snapshot changed" |
| Snapshot hashes validate, and no snapshot is duplicated | PASS  | `verify_run.py` "no duplicate snapshot"                              |
| Future stimuli stay private                             | PASS  | `verify_run.py` "no future stimuli through the API", against the API |
| Analysis completed for every cycle                      | PASS  | `verify_run.py` "analysis is complete"; cycles 1–24 analysed         |
| Checkpoint interviews at 0, 12, 24                      | PASS  | `verify_run.py` "checkpoint interviews exist"; 18 interviews stored  |
| All four metrics expose resolvable evidence             | PASS  | `verify_run.py` "metric evidence resolves"; 2,062 metric rows        |
| The Graveyard covers every eviction                     | PASS  | `verify_run.py` "graveyard covers every eviction"; 157 entries       |
| Model usage stayed within its limits                    | PASS  | `verify_run.py` "model usage stays within its limits"; 316 of 600    |
| A scheduler-triggered cycle completed autonomously      | PASS  | 95 run-cycle invocations against 24 committed cycles                 |
| Duplicate execution creates no duplicate                | PASS  | Cycle lock plus `attribute_not_exists`; refused fires committed none |
| Six-arm commits are atomic                              | PASS  | `TransactWriteItems`; no cycle has fewer than six snapshots          |
| No generated output was edited by hand                  | PASS  | Snapshots are conditional writes; every hash still verifies          |

## Scientific integrity

| Requirement                                                   | State | Evidence                                                                      |
| ------------------------------------------------------------- | ----- | ----------------------------------------------------------------------------- |
| Arm names never reach a prompt                                | PASS  | `test_pilot_blindness.py`, `assert_policy_blind` on every request             |
| The budget is the writer model's own tokenisation             | PASS  | ADR-013; `bedrock_converse_usage` recorded on the run and the export          |
| No fallback from an exact counter to an approximate one       | PASS  | `test_gateway_tokens.py`; the counter raises and nothing catches it           |
| The protocol is immutable once frozen                         | PASS  | `test_pilot_freeze.py`, 20 tests; digests recomputed on every load            |
| A frozen manifest cannot be rewritten, even by its own script | PASS  | `test_a_frozen_manifest_refuses_to_be_rewritten`; `make pilot-freeze` refuses |
| A modified protocol file causes launch rejection              | PASS  | `test_a_modified_protocol_file_is_refused`                                    |
| A different model ID causes launch rejection                  | PASS  | `test_a_different_model_identifier_is_refused`                                |
| A different token budget causes launch rejection              | PASS  | `test_a_different_token_budget_is_refused`                                    |
| A different prompt hash causes launch rejection               | PASS  | `test_a_different_prompt_hash_is_refused`                                     |
| No canonical result was edited by hand                        | PASS  | Snapshots are written under `attribute_not_exists`; hashes verify             |
| Raw chain-of-thought is never requested, stored, or shown     | PASS  | Structured output schemas carry no reasoning field                            |
| A local run is structurally marked as not a result            | PASS  | `run_kind`, `simulated`, export labels, footer provenance                     |

## The deployment

| Requirement                                       | State   | Evidence                                                                                                        |
| ------------------------------------------------- | ------- | --------------------------------------------------------------------------------------------------------------- |
| Both S3 buckets are private                       | PASS    | All four public-access blocks on; direct object GET returns 403                                                 |
| CloudFront serves the frontend over OAC           | PASS    | Bucket policy names the distribution; direct S3 is refused                                                      |
| Every CloudFront origin is S3 behind an OAC       | PASS    | CDK assertion walks all origins; no custom origin permitted                                                     |
| A restrictive CSP and security headers are served | PASS    | CSP, HSTS, nosniff, `DENY`, `no-referrer` on every response                                                     |
| CORS is restricted to configured origins          | PASS    | `access-control-allow-origin` is the distribution, never `*`                                                    |
| The public API has no mutation route              | PASS    | POST, PUT, PATCH, DELETE all 404; the read role holds no write action                                           |
| IAM is least-privilege, per function              | PASS    | Explicit statements, no `grantReadWriteData`; CDK assertions                                                    |
| Rate limits and a budget circuit breaker exist    | PASS    | `ModelCallLimits` checked before every call; reserved concurrency                                               |
| Administrative actions are protected              | PASS    | Arming needs IAM to change a function's environment; nothing public                                             |
| No credential or prompt appears in a log          | PASS    | Closed 13-field allowlist in `telemetry.py`; log grep                                                           |
| Scheduler defaults to disabled everywhere         | PASS    | `environments.ts`; CDK assertion on all three environments                                                      |
| Scheduler is disabled after completion            | PASS    | `preflight`: `attention-sink-production-cycle DISABLED`, execution off                                          |
| The read API survives a burst of visitors         | PARTIAL | Cap raised 20 → 100 and asserted in CDK; **awaiting one deploy**. At 20, a 24-request burst returned three 503s |

## The product

| Requirement                                  | State   | Evidence                                                                                                                                                       |
| -------------------------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| The exhibition contains no fixture data      | PASS    | `VITE_FIXTURE_MODE=false`; no fixture bundle                                                                                                                   |
| The page states what produced its words      | PASS    | Footer reads the run's own labels; flow 1-2b                                                                                                                   |
| Six minds are comparable at one cycle        | PASS    | Playwright flow 3, deployed                                                                                                                                    |
| Cycles 0, 12 and 24 are all reachable        | PASS    | Playwright flows 4 and 8, deployed                                                                                                                             |
| Graveyard, Timeline, Interviews, Echoes work | PASS    | Playwright flows 5–10, deployed                                                                                                                                |
| Evidence links resolve                       | PASS    | `verify_run.py` "metric evidence resolves"                                                                                                                     |
| Dreamer compression is shown accurately      | PASS    | Playwright flow 10; lineage resolves both ways                                                                                                                 |
| Methodology carries the required caveats     | PASS    | Playwright flows 11 and 11b, deployed                                                                                                                          |
| No fixture-mode indicator appears            | PASS    | Playwright flows 1-2 and 13, deployed                                                                                                                          |
| Mobile layout and keyboard navigation work   | PASS    | Playwright flows 13–14, mobile and desktop                                                                                                                     |
| Accessibility basics hold                    | PASS    | `accessibility.spec.ts`, both projects, deployed                                                                                                               |
| Safe error pages work                        | PASS    | 403/404 fall back to the client, which renders a stated error                                                                                                  |
| The dataset download works                   | PARTIAL | Behaviour, links and a followed-link assertion are written and tested; **awaiting the same deploy**. Until then the page lists the dataset without offering it |

## Engineering

| Requirement                                    | State | Evidence                                        |
| ---------------------------------------------- | ----- | ----------------------------------------------- |
| Local mode still works with no AWS credential  | PASS  | `make local-all`, 24 cycles, 26 checks          |
| One implementation, two adapter sets           | PASS  | `test_import_boundaries.py`; services unchanged |
| Strict typing across the repository            | PASS  | mypy strict, no ignores outside jsii boundaries |
| Coverage gate per package                      | PASS  | 95% floor, enforced in `pyproject.toml`         |
| No TODO, placeholder, or commented-out code    | PASS  | `ruff` rules plus review                        |
| The generated cost report survives `make lint` | PASS  | Generator emits prettier-stable markdown        |
