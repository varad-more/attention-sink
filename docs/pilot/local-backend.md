# The local backend

Phase 5 turns the in-memory pilot into a persistent application: SQLite, the local
filesystem, fixture models, and a local HTTP server. Everything above the adapter line
is what Phase 7 will run on AWS unchanged.

## What the pipeline does

```bash
make local-db-migrate    # create the database and apply every migration
make local-run-create    # create a run, seed six arms, interview at cycle 0
make local-cycle LOCAL_CYCLES=24
make local-analyze       # score every metric and store its evidence
make local-export        # write the seventeen-file dataset and its checksums
make local-verify        # check the run against every invariant it claims
make local-all           # all of the above, from an empty database
```

`make local-scheduler` simulates EventBridge: one cycle per tick, never two at once.
`make local-api` serves the read API. `make local-reset-demo` deletes a run and
refuses anything that is not `LOCAL_FIXTURE`.

## Where the adapter line falls

| Above the line (shared with AWS)  | Below the line (replaced in Phase 7)           |
| --------------------------------- | ---------------------------------------------- |
| domain, policies, protocol        | `packages/persistence` → DynamoDB              |
| `PilotService`, `AnalysisService` | local filesystem export → S3                   |
| repository **ports**              | fixture gateway → Bedrock                      |
| snapshot and API schemas          | `packages/api` app → API Gateway + Lambda      |
| metric definitions                | `scripts/run_local_scheduler.py` → EventBridge |

The ports live in `attention_sink.pilot.repositories`, with the application rather
than with the adapter, because an application owns its interfaces. The composition
root — the one module that picks which adapter satisfies them — is
`scripts/local_cli.py`, deliberately outside every package.
`tests/unit/test_import_boundaries.py` fails if an application package imports an
adapter; it caught exactly that during this phase, which is why the CLI moved.

## The atomic commit

One transaction does all of it, and any failure rolls back every part:

1. the run is at the version this caller read
2. the run is at exactly `cycle - 1`
3. the lock token is still ours
4. the prepared cycle matches the content hash presented
5. six immutable snapshot rows are inserted
6. six current-state rows are updated
7. the prepared cycle is marked committed
8. the run advances by exactly one cycle
9. the usage counters are updated
10. the lock is released by the same transaction that used it
11. the run is marked complete at the final cycle

Five arms that advanced and one that did not is no longer the same experiment, and
there is no repair for it after the fact — which is why this is one transaction and
not eleven writes.

**Immutability is a database trigger.** `cycle_snapshots` and `interviews` refuse
`UPDATE` outright, and `cycle_snapshots` refuses `DELETE` while its run still exists.
That guarantee survives code nobody has written yet, which a convention would not.

## Idempotency

A cycle is generated once, written as a `PreparedCycle`, hashed, and only then
committed. A retry that presents identical content reuses the record instead of
calling six writers again — and, more importantly, instead of generating a _different_
cycle for the same cycle number. Different content for one cycle is a
`PreparedCycleConflict`, not a retry.

The rules, all tested:

- a duplicate committed cycle changes nothing
- a matching prepared cycle is reused
- a conflicting prepared cycle fails loudly
- an expired lock may be replaced; an unexpired one may not
- a completed snapshot cannot be overwritten
- nothing can advance past the next expected cycle
- a checkpoint fired twice interviews nobody twice

Prepared cycles are never exposed through the API. They describe a cycle that has not
happened, and a reader who could see one could see the experiment's future.

## The metrics

Deterministic first, everywhere. A model is asked only where a rule genuinely cannot
decide, and the record says when one was asked.

**Origin Recall** normalises the answer, matches the fact's required terms, accepts a
configured variant, and only then — for a fact explicitly marked ambiguous — asks the
evaluator. A name is recalled or it is not. Reported unweighted and weighted, because
six right answers about clocks must not hide a forgotten name.

**Identity Drift** builds a document from Q01, Q02, Q03, Q08, and Q10 in a fixed
order, embeds it, and measures cosine distance from the same arm's cycle-0 document.
The pairwise matrix is computed once per unordered pair and written into both cells,
so symmetry is a property of the construction.

**Graveyard Echo** is a difference, not a similarity:
`echo_delta = forgotten_similarity - active_similarity`. Resemblance alone is a
property of the setting; what matters is being closer to what the arm cannot see than
to what it can. Only a delta over the versioned threshold costs a model call.

**Contradiction analysis** never scores admitted uncertainty as contradiction. An arm
that says it does not know is behaving better than one that guesses.

## The Graveyard

Derived from snapshots rather than stored beside them, so it cannot disagree with the
record it comes from. The distinction the whole view exists for is
`genuinely_inaccessible`: a memory a summary still carries has not been forgotten, and
counting it as a loss would make the summarising arm look like it forgets most and
remembers most at once.

## The read API

Read-only by construction: no mutating route is registered and a test asserts the
route table contains only `GET`. Administrative actions stay on the command line,
because an endpoint that could advance the experiment could advance it twice.

Filtered out of every response: prepared cycles, future stimuli, evaluator notes,
truth-ledger metadata, and prompt text. Published deliberately: prompt versions and
hashes, which is what makes a run reproducible without publishing the apparatus.

Responses use one envelope, carry `simulated: true` and the three provenance labels,
paginate where a list can grow, and carry an `ETag` plus a long cache only on records
that are actually immutable.

## Still not results

Every artefact of a local run is `LOCAL_FIXTURE`, `NON_CANONICAL`, and
`SIMULATED_MODEL_OUTPUTS`. The token budget remains a `PROVISIONAL_LOCAL_APPROXIMATION`
and the protocol remains `LOCAL_VALIDATED`. Phase 5 makes the application persistent;
it does not make its output evidence.
