# Local release readiness

What was verified, how, and what remains for AWS. Every figure below came from a
command in this repository, run against a database built from empty by
`make local-all`.

**The data this describes is `LOCAL_FIXTURE`, `NON_CANONICAL`, and
`SIMULATED_MODEL_OUTPUTS`.** It demonstrates that the application works. It is not
evidence about how any language model remembers.

## How to reproduce all of it

```bash
make pilot-local-release-check
```

That runs `make verify`, `make local-all`, `make pilot-local-build`, and
`make pilot-local-e2e` in order. The Playwright suite needs the API running
(`make local-api`) because it drives the real stack rather than a mock.

## What was verified

| #   | Requirement                        | Result | Evidence                                                                                                                                   |
| --- | ---------------------------------- | ------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Full 24-cycle SQLite run           | PASS   | `make local-all`: run reaches cycle 24/24, status `completed`                                                                              |
| 2   | Six snapshots per cycle            | PASS   | 144 snapshots; `verify_local_run.py` "arms share every completed cycle"                                                                    |
| 3   | Atomic commits                     | PASS   | `test_a_failed_commit_leaves_nothing_behind`, `test_one_cycle_commits_six_arms_and_advances_once`                                          |
| 4   | No duplicate cycles                | PASS   | `test_a_duplicate_cycle_invocation_does_not_advance_twice`; run version 24 after 24 commits                                                |
| 5   | No policy leakage                  | PASS   | `test_no_policy_name_reaches_the_writer` (7 cases), `test_no_public_character_name_reaches_the_writer` (4 cases)                           |
| 6   | No future-stimulus leakage         | PASS   | `test_no_future_stimulus_reaches_the_writer`; `verify_local_run.py` "no future stimuli through the API"; API refuses any uncommitted cycle |
| 7   | Memory budget compliance           | PASS   | Every snapshot `tokens_after ≤ 240`; `verify_local_run.py` "no budget violations"                                                          |
| 8   | Dreamer lineage                    | PASS   | 9 summaries, each with ≥2 parents; `verify_local_run.py` "dreamer lineage resolves"                                                        |
| 9   | Interviews at 0, 12, 24            | PASS   | 18 stored interviews; read-only checked by `test_interviews_never_touch_arm_state`                                                         |
| 10  | Four primary metrics with evidence | PASS   | 252 metric rows, each with rationale, evaluator version, calculation version                                                               |
| 11  | Graveyard                          | PASS   | 116 entries; compression distinguished from eviction and tested                                                                            |
| 12  | Timeline                           | PASS   | E2E flow 9; accessible SVG with a table carrying the same figures                                                                          |
| 13  | Export checksums                   | PASS   | 17 files; `shasum -a 256 -c` verifies all 16 plus the manifest                                                                             |
| 14  | Frontend production build          | PASS   | `npm run build`: 38 modules, 239.76 kB JS / 5.55 kB CSS                                                                                    |
| 15  | Accessibility                      | PASS   | 24 automated checks: landmarks, one h1 per page, skip link, labelled controls, text alternatives, status never by colour alone             |
| 16  | Playwright                         | PASS   | 62 passed, 2 skipped, across desktop and mobile projects                                                                                   |
| 17  | No AWS credentials required        | PASS   | No `boto3` client is constructed; `MODEL_MODE` defaults to fixture; import-boundary tests enforce the line                                 |
| 18  | Python checks                      | PASS   | `make verify`: lint, typecheck, tests, coverage gates                                                                                      |
| 19  | TypeScript checks                  | PASS   | `npm run lint`, `npm run typecheck`, `npm run test`                                                                                        |

## The numbers

```
run_local_pilot [local_fixture] completed, cycle 24/24
  144 cycle snapshots        18 interviews (cycles 0, 12, 24)
  171 model calls            {writer 144, interviewer 18, summarizer 9}
    0 evaluator calls          0 auditor calls
  252 metric rows           116 graveyard entries
  102 echo measurements     180 contradiction classifications
   17 export files, all checksums verify
   16 verification checks, all pass
```

Final active tokens against a 240-token budget: `arm_fifo` 234, `arm_lru` 238,
`arm_heavy` 238, `arm_sink` 240, `arm_random` 231, `arm_summary` 221 — identical to
the Phase 4 in-memory run, which is the cross-check that persistence and the frontend
changed the plumbing and not the experiment.

## Two defects this phase found and fixed

**One SQLite connection shared across a threadpool.** Phase 5 opened the database with
`check_same_thread=False` and a comment arguing it was safe because writes were
serialised. That reasoning was wrong: the flag silences the thread check but does not
make a connection re-entrant, and the read API serves synchronous endpoints from
Starlette's threadpool. Under Playwright the API raised
`sqlite3.InterfaceError: bad parameter or other API misuse`. Fixed with one connection
per thread and a `busy_timeout`; concurrency between connections is WAL's job, which
it does well.

**Pages had no heading while loading or failing.** Every route returned its `h1` only
after data arrived, so a slow or failed load left the page with no heading at all —
worst exactly when a reader most needs to know where they are. Every route now renders
its heading first and puts the loading or error state beneath it.

Both were found by the accessibility and E2E suites rather than by review.

## What is deferred to AWS

Nothing local is deferred. Everything below is DEFERRED_TO_AWS because it cannot be
demonstrated without an account, and each has a local adapter standing in for it now.

- DynamoDB repository (SQLite adapter satisfies the same protocol today)
- S3 export storage (local filesystem export today)
- Bedrock model invocation and exact `CountTokens` calibration (fixture gateway today)
- Lambda handlers and API Gateway (local ASGI app today)
- EventBridge Scheduler (`scripts/run_local_scheduler.py` today)
- CDK deployment, CloudFront, custom domain
- The canonical run, and every figure that would come from it

## What a local green run does and does not mean

It means the application sequences a cycle correctly, commits six arms atomically,
refuses to advance twice, keeps its invariants, computes its metrics with evidence,
serves only completed data, and exports a dataset somebody else can verify.

It does not mean anything about model behaviour. The generations are deterministic
fixtures, the token budget is a local approximation, and the protocol is
`LOCAL_VALIDATED` rather than frozen. Both remain true until Phase 8 calibration.
