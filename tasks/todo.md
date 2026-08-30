# Pilot Phase 5 - transactional local persistence, read API, analysis, export

Local-First Remaining-Phases Override binding. No AWS credentials, no AWS calls, no
React frontend, no CDK.

## Shape

- `packages/pilot/repositories.py` - twenty-six ports, provider independent.
- `packages/pilot/service.py` - lock, load, stage or reuse, prepare, commit, release.
- `packages/persistence` - the SQLite adapter and its migrations.
- `packages/analysis` - the four primary metrics, the Graveyard, the secondary
  metrics, and the dataset export.
- `packages/api` - the local read API.
- `scripts/local_cli.py` - the composition root, deliberately outside every package.

## Plan

- [x] 1. Repository ports and the records they carry.
- [x] 2. SQLite adapter with migrations and immutable snapshot rows.
- [x] 3. One-transaction six-arm commit, all-or-nothing.
- [x] 4. PreparedCycle: staged once, hashed, reused across retries.
- [x] 5. Lock tokens, expiry, invocation ids, idempotency rules.
- [x] 6. `scripts/run_local_scheduler.py`.
- [x] 7. Persisted checkpoint interviews, still read-only.
- [x] 8. Origin Recall, deterministic first.
- [x] 9. Identity Drift and the symmetric pairwise matrix.
- [x] 10. Graveyard, distinguishing compression from eviction.
- [x] 11. Graveyard Echo and its six categories.
- [x] 12. Contradiction analysis; uncertainty is never a contradiction.
- [x] 13. Thirteen secondary metrics, no model calls.
- [x] 14. The local read API, completed data only.
- [x] 15. The seventeen-file dataset export.
- [x] 16. `scripts/verify_local_run.py`.
- [x] 17. `make local-*` commands.
- [x] 18. Tests for all twenty-six subjects.

## Review

**The import-boundary test found the one real architectural mistake.** The first draft
put the local commands in `attention_sink.pilot.local`, where they imported the SQLite
adapter and the analysis package. An application that imports its own adapter has no
adapter line left to move in Phase 7. The commands moved to `scripts/local_cli.py`;
the pilot package kept only `build_configuration`, which stays inside the boundary.
This is exactly what that test exists for and it is the second phase running in which
it has earned its place.

**Immutability is a database trigger, not a convention.** `cycle_snapshots` and
`interviews` refuse `UPDATE` outright. A future adapter method that tried to revise a
committed snapshot fails at the storage layer rather than passing review.

**The commit is one transaction because a partial cycle has no repair.** Five arms
that advanced and one that did not is a different experiment, and no later code can
reconstruct which. Eleven checks and writes, rolled back entirely on anything.

**Checkpoint spend was silently missing.** A checkpoint runs after the commit that
snapshots usage, so the first working version undercounted the run by eighteen
interviewer calls - a fifth of the total. `add_usage` folds them in afterwards. Caught
by comparing the persisted run's totals against the Phase 4 in-memory run, which is
also the cross-check that persistence changed the storage and not the experiment.

**Deterministic scoring, with the model kept out of it.** Origin Recall asks an
evaluator only for a fact explicitly marked ambiguous; Graveyard Echo asks only when
the delta crosses a versioned threshold; contradiction analysis asks only when no rule
applies. Tests assert the _refusals_: a missing name that reached an evaluator would
fail `test_an_absent_answer_scores_zero_without_asking_a_model`.

**Compression is not forgetting.** `genuinely_inaccessible` is false for any memory a
summary still carries, and the echo analysis reports `COMPRESSED_ECHO` rather than a
reconstruction. Without that, the one arm designed to retain information would look
like the one most haunted by losing it.

**`check_same_thread=False` is a deliberate, narrow exception.** The read API serves
sync endpoints from a threadpool. It is safe only because every write goes through one
`BEGIN IMMEDIATE` transaction and the API never writes; a future adapter that wrote
from several threads would need a connection per thread instead.

**One test was made less fragile rather than more clever.** The runner-script check
originally spawned a subprocess, which re-resolved the editable install and failed
whenever the local venv was in the state the macOS `UF_HIDDEN` defect leaves it in. It
now parses arguments in-process: same guarantee, no dependence on the environment.
