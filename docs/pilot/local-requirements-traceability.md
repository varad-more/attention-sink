# Local requirements traceability

Every requirement of Phases 4–6, where it is implemented, where it is proved, and its
status. `DEFERRED_TO_AWS` is used only where a requirement cannot be met without an
AWS account; nothing local is deferred.

Statuses: **PASS** · **FAIL** · **PARTIAL** · **DEFERRED_TO_AWS**

## Scientific invariants

| #   | Invariant                                 | Implementation                          | Evidence                                                                                               | Status |
| --- | ----------------------------------------- | --------------------------------------- | ------------------------------------------------------------------------------------------------------ | ------ |
| 1   | Six arms begin with identical seeds       | `PilotService.create_run`               | `test_every_arm_is_initialised_identically`, `test_a_created_run_seeds_six_identical_arms`             | PASS   |
| 2   | Same stimulus per cycle for all arms      | `PilotEngine.prepare_cycle`             | `test_every_arm_receives_the_same_stimulus_in_a_cycle`; `verify_local_run.py`                          | PASS   |
| 3   | Same writer configuration                 | `PilotRunConfiguration`                 | `test_the_pilot_configures_exactly_the_six_canonical_arms`                                             | PASS   |
| 4   | Writer never learns policy or public name | `present_memories`, `arms.ts`           | `test_no_policy_name_reaches_the_writer`, `test_no_public_character_name_reaches_the_writer`           | PASS   |
| 5   | Writer never sees other arms              | `PilotEngine.generate_arm_cycle`        | `test_no_other_arms_memories_reach_the_writer`                                                         | PASS   |
| 6   | Writer never sees future stimuli          | engine passes one stimulus              | `test_no_future_stimulus_reaches_the_writer`                                                           | PASS   |
| 7   | Writer never sees the truth ledger        | ledger not passed to gateway            | `test_no_truth_ledger_metadata_reaches_the_writer`                                                     | PASS   |
| 8   | Writer receives only active memories      | `present_memories(require_active=True)` | `PromptLeakError` on any retired memory                                                                | PASS   |
| 9   | Application policy engine rebalances      | `PilotEngine.rebalance_arm_memory`      | `packages/policies` suite                                                                              | PASS   |
| 10  | Active memory stays in budget             | policy + snapshot validator             | `test_every_arm_stays_within_the_provisional_budget`                                                   | PASS   |
| 11  | Evicted memories never return             | `MemoryState`                           | `test_a_retired_memory_never_returns_to_the_active_set`                                                | PASS   |
| 12  | Dreamer summaries cost the same budget    | `SummarizationPolicy`                   | `test_a_dreamer_summary_costs_the_same_budget_as_any_other_memory`                                     | PASS   |
| 13  | Dreamer lineage preserved                 | `MemoryKind.SUMMARY` + parents          | `test_every_dreamer_summary_keeps_its_lineage`                                                         | PASS   |
| 14  | Interviews read-only                      | `run_checkpoint`                        | `test_interviews_never_touch_arm_state`, `test_an_interview_never_becomes_a_memory`                    | PASS   |
| 15  | Completed snapshots immutable             | SQLite ABORT trigger                    | `test_a_completed_snapshot_cannot_be_modified`                                                         | PASS   |
| 16  | Six results commit together               | `commit_cycle` transaction              | `test_a_cycle_commits_all_six_arms_or_none`                                                            | PASS   |
| 17  | A failed cycle advances nothing           | staged commit + rollback                | `test_one_failing_arm_prevents_every_arm_from_advancing`, `test_a_failed_commit_leaves_nothing_behind` | PASS   |
| 18  | Duplicate execution is idempotent         | `PreparedCycle` + lock                  | `test_a_duplicate_cycle_invocation_does_not_advance_twice`                                             | PASS   |
| 19  | Every score retains evidence              | `MetricEvidence`                        | `test_all_four_primary_metrics_are_stored_with_evidence`                                               | PASS   |
| 20  | Raw chain-of-thought never stored         | structured schemas only                 | no field carries it; schemas forbid extras                                                             | PASS   |

## Phase 4 — protocol and engine

| Requirement                               | Implementation                      | Evidence                                                              | Status |
| ----------------------------------------- | ----------------------------------- | --------------------------------------------------------------------- | ------ |
| Versioned protocol files with digests     | `experiment/pilot/*.yaml`           | `make pilot-validate`                                                 | PASS   |
| Status lifecycle DRAFT → … → RETIRED      | `ProtocolStatus`                    | `test_no_command_may_promote_a_protocol_past_local_validation`        | PASS   |
| Protocol stops at LOCAL_VALIDATED         | `promote_documents`                 | `test_the_committed_protocol_is_not_frozen`                           | PASS   |
| Twelve seeds, 24 stimuli, 10 questions    | protocol files                      | `make pilot-validate`                                                 | PASS   |
| Provisional local token budget            | `scripts/calibrate_local_budget.py` | `docs/pilot/local-token-calibration.md`                               | PASS   |
| Model-call budget refused before the call | `ModelCallBudget.spend`             | `test_a_call_past_the_limit_is_refused_before_the_gateway_is_touched` | PASS   |
| Canonical-JSON snapshot hashing           | `canonical.py`                      | `test_every_snapshot_hash_is_stable_and_self_verifying`               | PASS   |
| Protocol modification detection           | `drifted()`, `manifest_drift`       | `test_editing_a_validated_protocol_is_detected`                       | PASS   |
| `run_kind` on every artefact              | `RunKind`                           | `test_every_local_artefact_says_it_is_simulated_and_non_canonical`    | PASS   |

## Phase 5 — persistence, analysis, API, export

| Requirement                              | Implementation                   | Evidence                                                                | Status |
| ---------------------------------------- | -------------------------------- | ----------------------------------------------------------------------- | ------ |
| Provider-independent repository ports    | `pilot/repositories.py`          | `test_import_boundaries`                                                | PASS   |
| SQLite adapter with migrations           | `packages/persistence`           | `test_migrations_are_applied_once_and_are_idempotent`                   | PASS   |
| One-transaction six-arm commit           | `commit_cycle`                   | `test_a_failed_commit_leaves_nothing_behind`                            | PASS   |
| PreparedCycle reuse across retries       | `PilotService._prepare`          | `test_a_retry_reuses_the_prepared_cycle_instead_of_calling_six_writers` | PASS   |
| Conflicting PreparedCycle refused        | `store_prepared_cycle`           | `test_a_conflicting_prepared_cycle_is_refused`                          | PASS   |
| Lock tokens, expiry, invocation ids      | `acquire_cycle_lock`             | `test_an_expired_lock_may_be_replaced`                                  | PASS   |
| Local scheduler simulator                | `scripts/run_local_scheduler.py` | one cycle per tick, verified by hand and by `--help` parse test         | PASS   |
| Origin Recall, deterministic first       | `analysis/metrics.py`            | `test_an_absent_answer_scores_zero_without_asking_a_model`              | PASS   |
| Identity Drift, symmetric matrix         | `pairwise_distance_matrix`       | `test_the_pairwise_matrix_is_symmetric_with_a_zero_diagonal`            | PASS   |
| Graveyard, compression ≠ eviction        | `analysis/graveyard.py`          | `test_the_graveyard_distinguishes_eviction_from_compression`            | PASS   |
| Graveyard Echo, six categories           | `analysis/echo.py`               | `test_a_crossing_delta_is_categorised_by_the_evaluator`                 | PASS   |
| Contradiction analysis                   | `analysis/contradiction.py`      | `test_admitted_uncertainty_is_never_a_contradiction`                    | PASS   |
| Thirteen deterministic secondary metrics | `secondary_metrics`              | `test_the_secondary_metrics_need_no_model_call`                         | PASS   |
| Local read API, completed data only      | `packages/api`                   | `test_the_api_refuses_a_cycle_that_has_not_been_committed`              | PASS   |
| No public write routes                   | route table                      | `test_the_api_registers_no_write_route`                                 | PASS   |
| Eighteen-file dataset export             | `analysis/export.py`             | `test_the_export_writes_every_documented_file`                          | PASS   |
| Checksum verification                    | `verify_checksums`               | `test_a_corrupted_export_is_detected`                                   | PASS   |
| Run verification command                 | `scripts/verify_local_run.py`    | 16/16 checks pass                                                       | PASS   |

## Phase 6 — the exhibition

| Requirement                                    | Implementation                              | Evidence                                                             | Status |
| ---------------------------------------------- | ------------------------------------------- | -------------------------------------------------------------------- | ------ |
| Validated frontend configuration               | `src/config.ts`                             | `config.test.ts` (5 cases)                                           | PASS   |
| Production-like build fails without API config | `resolveConfig`                             | `test('refuses a production build…')`                                | PASS   |
| No experiment JSON imported into components    | `src/api/client.ts` is the only source      | no fixture import exists in `src/`                                   | PASS   |
| Frontend types match API schemas               | `src/api/types.ts`                          | E2E flows read live responses                                        | PASS   |
| Seven routes                                   | `App.tsx`                                   | `test('every documented route is served')`                           | PASS   |
| Landing page copy and badges                   | `routes/SixMinds.tsx`                       | E2E flow 1–2                                                         | PASS   |
| Six Minds with public names                    | `arms.ts`, `MindCard.tsx`                   | E2E flow 3                                                           | PASS   |
| Public names only in presentation              | `arms.ts`                                   | `arms.test.ts`                                                       | PASS   |
| Synchronized cycle selector                    | `/cycle/:cycle`                             | E2E flow 4                                                           | PASS   |
| Focus mode and comparison table                | `SixMinds.tsx`                              | E2E flow 3                                                           | PASS   |
| Graveyard with filters in the URL              | `routes/Graveyard.tsx`                      | E2E flow 10 uses a filter URL                                        | PASS   |
| Memory detail and lineage                      | `routes/MemoryDetail.tsx`                   | E2E flows 5–6, 10                                                    | PASS   |
| No prompts or evaluator instructions exposed   | API projections                             | `test_a_cycle_response_publishes_prompt_versions_but_no_prompt_text` | PASS   |
| Timeline scrubber, six tracks                  | `routes/Timeline.tsx`                       | E2E flow 9                                                           | PASS   |
| Accessible SVG with table fallback             | `Timeline.tsx`                              | `test('the timeline chart carries a text alternative and a table')`  | PASS   |
| Interviews, checkpoint and question selectors  | `routes/Interviews.tsx`                     | E2E flow 8                                                           | PASS   |
| Read-only interview notice                     | `Interviews.tsx`                            | E2E flow 8 asserts the sentence                                      | PASS   |
| Graveyard Echo view with careful language      | `routes/Echoes.tsx`                         | E2E flow 7 asserts "not an access"                                   | PASS   |
| Methodology with all eight limitations         | `routes/Methodology.tsx`                    | E2E flow 11                                                          | PASS   |
| Polling, pauses when hidden                    | `api/hooks.ts`                              | `useDocumentVisible`; historical cycles never polled                 | PASS   |
| Immutable responses cached                     | `ApiClient` cache                           | committed cycles fetched once                                        | PASS   |
| Connection failure surfaced safely             | `ErrorState`                                | says what command to run                                             | PASS   |
| No WebSockets                                  | —                                           | none in `package.json` or `src/`                                     | PASS   |
| Accessibility                                  | `styles.css`, semantic markup               | 24 automated checks                                                  | PASS   |
| One-command local demo                         | `make pilot-local-demo`                     | target exists and composes the pipeline                              | PASS   |
| Playwright flows 1–14                          | `apps/web/e2e`                              | 62 passed, 2 skipped                                                 | PASS   |
| Local release artifacts                        | build, database, export, manifests, reports | `make pilot-local-release-check`                                     | PASS   |

## Deferred to AWS

| Requirement                          | Local stand-in                    | Status          |
| ------------------------------------ | --------------------------------- | --------------- |
| DynamoDB repository                  | SQLite adapter, same protocol     | DEFERRED_TO_AWS |
| S3 export storage                    | local filesystem export           | DEFERRED_TO_AWS |
| Bedrock invocation                   | fixture gateway                   | DEFERRED_TO_AWS |
| Exact `CountTokens` calibration      | `heuristic-v1` provisional budget | DEFERRED_TO_AWS |
| Lambda handlers, API Gateway         | local ASGI app                    | DEFERRED_TO_AWS |
| EventBridge Scheduler                | `run_local_scheduler.py`          | DEFERRED_TO_AWS |
| CDK deployment, CloudFront, domain   | —                                 | DEFERRED_TO_AWS |
| Canonical run and its findings       | fixture run, marked non-canonical | DEFERRED_TO_AWS |
| Protocol `AWS_CALIBRATED` → `FROZEN` | `LOCAL_VALIDATED`                 | DEFERRED_TO_AWS |

No requirement is FAIL or PARTIAL.
