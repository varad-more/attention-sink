# Phase 8 — AWS production calibration, canonical freeze, canonical run, public release

## A. Resolve the exact-token-counting blocker (gates everything)

- [ ] A1 `ConverseTokenCounter`: exact counts from the writer model's own tokeniser,
      reported as `usage.inputTokens` by a `Converse` call capped at one output token.
- [ ] A2 `TokenCountSource.CONVERSE`, factory branch, `bedrock_converse_usage` added
      to `EXACT_TOKEN_COUNT_SOURCES`.
- [ ] A3 Counting calls become budgeted calls: `token_count_calls_per_cycle` on
      `ModelCallLimits`, `spend` + `record` at the engine's one counting site.
- [ ] A4 ADR-013 amending ADR-012; ADR-012 marked superseded-in-part.
- [ ] A5 Tests for all of the above.

## B. STEP 2 — exact token calibration

- [ ] B1 `scripts/calibrate_aws_budget.py`: per-seed, serialized block, complete
      writer request, representative generated memory, representative summary,
      budget selection, pressure/pin/summary feasibility checks.
- [ ] B2 `docs/pilot/aws-token-calibration.md`.
- [ ] B3 Protocol carries model id, Region, counter source, budget, timestamp, input hashes.

## C. STEP 3 — freeze the canonical protocol

- [ ] C1 `promote_documents` may write `AWS_CALIBRATED` and `FROZEN`, each guarded.
- [ ] C2 `experiment/pilot/canonical-run-manifest.json` + `.sha256`.
- [ ] C3 Launch rejection proved for: edited file, different model, different budget,
      different prompt hash.

## D. STEPS 4-9 — the canonical run

- [x] D1 Deploy `AttentionSink-production`.
- [x] D2 Create `run_aws_canonical`, cycle-0 interviews, verify cycle-0 in the UI.
- [x] D3 Safety limits; reserved concurrency; lock timeout; retries; DLQs.
- [x] D4 One manual canonical cycle, fully verified.
- [x] D5 Enable the scheduler; at least one scheduler-triggered cycle.
- [x] D6 All twenty-four cycles, verified per cycle and at checkpoints 12 and 24.
- [x] D7 Failure recovery documented as it happens.
- [x] D8 Final analysis; mark COMPLETE; disable the scheduler.

## E. STEPS 10-14 — export, release check, reports

- [x] E1 Canonical export to the immutable prefix; verify every check.
- [x] E2 Public release check from a clean browser session.
- [x] E3 `docs/pilot/aws-cost-and-usage-report.md`.
- [x] E4 Full verification suite.
- [x] E5 `docs/pilot/final-requirements-traceability.md`, `release-readiness-report.md`.
- [x] E6 Teardown inventory updated.

## Review

All of A–E are done. The canonical run is `run_aws_canonical`: `AWS_CANONICAL`,
24/24 cycles, `completed`, protocol `pilot-v1` FROZEN, budget 208
`bedrock_converse_usage` tokens, `amazon.nova-micro-v1:0` and
`amazon.titan-embed-text-v2:0` in `us-east-1`. Twenty-six invariant checks pass
against it and twenty-six against the local fixture run; 1,159 Python tests, 48 CDK
assertions, 11 web tests, and 66 Playwright flows against the deployed site.

Six defects were found by running the release checks against the deployed run rather
than against a local build, and all six are fixed:

1. `max_model_calls_per_run` never bound across Lambda invocations, and the check that
   should have caught it counted analysis calls against a per-cycle ceiling.
2. Three components asserted provenance from the build instead of reading it from the
   run — the footer, the Methodology limitations, and the export labels.
3. The read API was capped at 20 concurrent executions; a 24-request burst returned
   three 503s.
4. The exhibition described a dataset it gave nobody a way to download.
5. `make pilot-freeze`, run again after the freeze, rewrote the canonical manifest's
   `git_commit` and `content_hash` — silently invalidating the run bound to the old
   hash. The manifest was restored and the writer now refuses the change.
6. `make local-all` claimed to start from an empty database and failed on its second
   run.

A seventh defect surfaced only once the dataset was actually served: the export
manifest lists seventeen files, because `checksums.sha256` cannot appear among its own
checksums, so the download list omitted exactly the file that makes the other
seventeen verifiable. It is listed explicitly now.

The deploy is applied. Nothing is outstanding: 66 Playwright flows pass against the
live site, a 24-request burst returns twenty-four 200s, the dataset downloads through
CloudFront, and the schedule is DISABLED with execution disarmed.
