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

- [ ] D1 Deploy `AttentionSink-production`.
- [ ] D2 Create `run_aws_canonical`, cycle-0 interviews, verify cycle-0 in the UI.
- [ ] D3 Safety limits; reserved concurrency; lock timeout; retries; DLQs.
- [ ] D4 One manual canonical cycle, fully verified.
- [ ] D5 Enable the scheduler; at least one scheduler-triggered cycle.
- [ ] D6 All twenty-four cycles, verified per cycle and at checkpoints 12 and 24.
- [ ] D7 Failure recovery documented as it happens.
- [ ] D8 Final analysis; mark COMPLETE; disable the scheduler.

## E. STEPS 10-14 — export, release check, reports

- [ ] E1 Canonical export to the immutable prefix; verify every check.
- [ ] E2 Public release check from a clean browser session.
- [ ] E3 `docs/pilot/aws-cost-and-usage-report.md`.
- [ ] E4 Full verification suite.
- [ ] E5 `docs/pilot/final-requirements-traceability.md`, `release-readiness-report.md`.
- [ ] E6 Teardown inventory updated.
