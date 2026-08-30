# Final requirements traceability

Every requirement the pilot was given, and where the evidence that it holds actually
lives. One row per requirement, four possible states, and no row whose evidence is
"it looked right".

**PASS** — verified by something that would fail if it stopped holding.
**PARTIAL** — holds under a stated limit; the limit is named in the row.
**FAIL** — does not hold.
**BLOCKED** — cannot be established here, and why.

Run under test: `run_aws_canonical`, `AWS_CANONICAL`, protocol `pilot-v1` FROZEN,
`amazon.nova-micro-v1:0` in `us-east-1`, budget 208 `bedrock_converse_usage` tokens.

## The six policy invariants

| #   | Invariant                                            | State | Evidence                                                                        |
| --- | ---------------------------------------------------- | ----- | ------------------------------------------------------------------------------- |
| 1   | Every arm ends a cycle inside the budget             | PASS  | `verify_run.py` "no budget violations", both runs                               |
| 2   | Only the mechanism differs between arms              | PASS  | `test_pilot_blindness.py`; one shared stimulus per cycle, verified per cycle    |
| 3   | The pinned memory never leaves `arm_sink`            | PASS  | `verify_run.py` "pinned memory survives"                                        |
| 4   | A retired memory never returns                       | PASS  | `verify_run.py` "no forgotten memory returned"                                  |
| 5   | The stochastic arm replays from its recorded seed    | PASS  | `verify_run.py` "random provenance replays"; property tests in `tests/property` |
| 6   | A summary names at least two parents, all resolvable | PASS  | `verify_run.py` "dreamer lineage resolves", "compression records are distinct"  |

## Scientific integrity

| Requirement                                               | State | Evidence                                                             |
| --------------------------------------------------------- | ----- | -------------------------------------------------------------------- |
| Arm names never reach a prompt                            | PASS  | `test_pilot_blindness.py`, `assert_policy_blind` on every request    |
| The budget is the writer model's own tokenisation         | PASS  | ADR-013; `bedrock_converse_usage` recorded on the run and the export |
| No fallback from an exact counter to an approximate one   | PASS  | `test_gateway_tokens.py`; the counter raises and nothing catches it  |
| The protocol is immutable once frozen                     | PASS  | `test_pilot_freeze.py`, 18 tests; digests recomputed on every load   |
| A canonical run must match the frozen manifest            | PASS  | `require_canonical_launch`, four rejection tests                     |
| No canonical result was edited by hand                    | PASS  | Snapshots are written under `attribute_not_exists`; hashes verify    |
| Raw chain-of-thought is never requested, stored, or shown | PASS  | Structured output schemas carry no reasoning field                   |
| A local run is structurally marked as not a result        | PASS  | `run_kind`, `simulated`, export labels, footer provenance            |

## The deployment

| Requirement                                       | State | Evidence                                                             |
| ------------------------------------------------- | ----- | -------------------------------------------------------------------- |
| Both S3 buckets are private                       | PASS  | All four public-access blocks on; direct object GET returns 403      |
| CloudFront serves the frontend over OAC           | PASS  | Bucket policy names the distribution; direct S3 is refused           |
| A restrictive CSP and security headers are served | PASS  | CSP, HSTS, nosniff, `DENY`, `no-referrer` on every response          |
| CORS is restricted to configured origins          | PASS  | `access-control-allow-origin` is the distribution, never `*`         |
| The public API has no mutation route              | PASS  | Every mutating verb returns 404; the read role holds no write action |
| IAM is least-privilege, per function              | PASS  | Explicit statements, no `grantReadWriteData`; CDK assertions         |
| Rate limits and a budget circuit breaker exist    | PASS  | `ModelCallLimits` checked before every call; reserved concurrency    |
| Administrative actions are protected              | PASS  | Arming needs IAM to change a function's environment; nothing public  |
| No credential or prompt appears in a log          | PASS  | Closed 13-field allowlist in `telemetry.py`; log grep                |
| Scheduler defaults to disabled everywhere         | PASS  | `environments.ts`; CDK assertion on all three environments           |

## The product

| Requirement                                  | State | Evidence                                           |
| -------------------------------------------- | ----- | -------------------------------------------------- |
| The exhibition contains no fixture data      | PASS  | `VITE_FIXTURE_MODE=false`; no fixture bundle       |
| The page states what produced its words      | PASS  | Footer reads the run's own labels; Playwright flow |
| Six minds are comparable at one cycle        | PASS  | Playwright flow 3                                  |
| Graveyard, Timeline, Interviews, Echoes work | PASS  | Playwright flows 5-10                              |
| Evidence links resolve                       | PASS  | `verify_run.py` "metric evidence resolves"         |
| Methodology carries the required caveats     | PASS  | Playwright flow 11                                 |
| The dataset download works                   | PASS  | Playwright flow 12                                 |
| Mobile layout and keyboard navigation work   | PASS  | Playwright flows 13-14, mobile project             |
| Accessibility basics hold                    | PASS  | `accessibility.spec.ts`, both projects             |

## Engineering

| Requirement                                   | State | Evidence                                        |
| --------------------------------------------- | ----- | ----------------------------------------------- |
| Local mode still works with no AWS credential | PASS  | `make local-all`, 24 cycles, 23 checks          |
| One implementation, two adapter sets          | PASS  | `test_import_boundaries.py`; services unchanged |
| Strict typing across the repository           | PASS  | mypy strict, no ignores outside jsii boundaries |
| Coverage gate per package                     | PASS  | 95% floor, enforced in `pyproject.toml`         |
| No TODO, placeholder, or commented-out code   | PASS  | `ruff` rules plus review                        |
