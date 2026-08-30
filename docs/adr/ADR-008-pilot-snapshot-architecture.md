# ADR-008-pilot. Pilot V1 runs one cycle engine over immutable snapshots

Status: Accepted, 2026-08-29. Scoped to Pilot V1.

> **A note on the number.** This record is named `ADR-008-pilot` by the Pilot Scope
> Override, and the repository already has an
> [ADR-008](008-budget-token-accounting.md) about budget token accounting. They are
> unrelated and neither supersedes the other. The filename and heading carry the
> `-pilot` suffix so the two can never be cited as the same decision; the numbering
> collision is recorded here rather than resolved by renumbering, because renumbering
> an accepted ADR would break every existing citation of it.

## Context

The architecture this repository was designed against is a distributed one.
[ADR-002](002-event-ledger-and-projections.md) puts an append-only event ledger at
the centre and derives every read model from it.
[ADR-003](003-step-functions-standard-workflow.md) sequences a cycle as a Step
Functions Standard Workflow with a six-arm inline map. Together they describe a system
that can survive a partial failure mid-cycle, replay a run from first principles, and
scale to more arms and longer runs than one process can hold.

None of that is wrong. All of it is unbuilt, and the pilot needs to answer a question
that comes first: **do six memory mechanisms, given identical seeds and identical
stimuli under one budget, actually diverge?** If they do not, the ledger and the
workflow are infrastructure for a null result.

Building the distributed version first would mean committing to an event schema, a
projection strategy, and a state-machine definition before a single run had shown what
is worth recording. Every one of those is expensive to change once a run exists that
was written under it.

## Decision

Pilot V1 runs the experiment as **one in-process cycle engine writing immutable
snapshots**, orchestrated locally, with a Lambda orchestrator as the deployment target
when the pilot leaves the laptop.

Concretely:

1. **One engine, not a workflow.** `attention_sink.pilot.engine.PilotEngine` owns the
   sequence: prepare the cycle, generate six arms, rebalance each, stage all six,
   validate across them, and only then advance. There is no state machine, no step
   boundary, and no distributed transaction.

2. **Snapshots, not events.** Each arm-cycle produces one `ArmCycleSnapshot` holding
   everything that produced it — what the arm held going in, what it wrote, what it
   claimed, what the mechanism decided, what it lost, and what it holds coming out.
   The whole run is one `RunSnapshot`. Nothing has to be folded to learn what an arm
   remembered on cycle 17.

3. **Canonical hashes instead of a ledger's ordering guarantees.** Every snapshot
   carries a SHA-256 digest over its own canonical JSON. Two processes that ran the
   same cycle produce the same hash, so a divergent replay is visible at the cycle it
   diverged. That is the property the ledger was going to buy, obtained more cheaply
   at pilot scale.

4. **Atomic six-arm advancement.** A cycle is staged in full and committed in one
   assignment. An arm that fails takes the cycle down and leaves all six states
   exactly as they were. This is the invariant the Step Functions map was going to
   enforce with a failure path; here it is a `try` and a single dictionary swap.

5. **No persistence in this phase.** The engine holds the run in memory and hands back
   records; `export.py` writes a directory with a checksum manifest. There is no
   DynamoDB, no S3, and no API.

## What this defers, and what it does not change

**Deferred for Pilot V1, not withdrawn.** ADR-002 and ADR-003 remain valid designs for
V2 and are the expected shape once the pilot has shown the arms separate and the run
needs to outgrow one process. Nothing here argues they are wrong; the argument is only
about order. Both records stay in place, unamended.

**Unchanged.** Every scientific invariant this repository is built on survives intact,
because none of them was ever a property of the transport:

- Six arms differ **only** in the mechanism that decides what to forget
  ([ADR-001](001-application-level-memory.md)).
- The writer is blind to the policy governing it
  ([ADR-004](004-policy-blind-writer-and-evaluator.md),
  [ADR-010](010-opaque-memory-labels-in-prompts.md)). The engine adds cross-arm
  blindness: no arm's request carries another arm's memories, a later stimulus, or a
  line from the truth ledger.
- Model identifiers come from configuration, never from a default
  ([ADR-006](006-model-ids-from-configuration.md)).
- The budget is denominated in a versioned counter
  ([ADR-008](008-budget-token-accounting.md)) and, outside fixture mode, in the
  model's own token counts ([ADR-011](011-exact-token-counts-in-production.md)).
- Compression is planned by the mechanism and written by a model, in two stages
  ([ADR-009](009-two-stage-compression.md)). The pilot's Dreamer is that mechanism;
  it is not a second one.
- A run's protocol is frozen and hashed before it can be canonical
  ([ADR-005](005-immutable-canonical-run-and-forks.md)).

## Consequences

**Good.** The whole experiment runs on a laptop with no AWS account, in under a
second, and is reproducible byte for byte. A protocol change is one YAML edit, a
re-freeze, and a re-run. The record of a cycle is one JSON object a person can read.

**Bad.** A run that dies mid-cycle loses that cycle; there is no resume. Everything is
bounded by one process's memory, which at twenty-four cycles and six arms is not close
to a constraint and at two hundred cycles would be. Snapshots duplicate state that an
event stream would store once — the export of a twenty-four cycle run is a few
megabytes, which is a cost worth paying and would not be at a hundred times the size.

**Risky.** The snapshot shape is not the event shape. Moving to V2 means writing a
translation from snapshots to events, or accepting that pilot runs and V2 runs are
read by different code. That is a real cost, and it is the price of not guessing the
event schema before the first run.

## When to revisit

- A pilot run has shown the arms diverge, and the experiment needs more arms, longer
  runs, or more than one run at a time.
- A cycle becomes expensive enough that losing one to a crash matters.
- Someone other than the person who ran it needs to query a run without loading it.

Any of those is the signal to build ADR-002 and ADR-003 as designed.
