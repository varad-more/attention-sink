# Pilot scope

What the pilot is, what it deliberately is not, and where it differs from the
production-scale design the rest of this repository was written against.

The pilot exists to answer one question before anything is built on top of it: **given
identical seed memories, identical stimuli, and one fixed active-memory budget, do six
memory mechanisms actually diverge?** Everything below is scoped to making that
question answerable on a laptop, reproducibly, with no AWS account.

## The experiment

|              |                                                                                |
| ------------ | ------------------------------------------------------------------------------ |
| Protocol     | `pilot-v1`                                                                     |
| Seed world   | Station Kestrel, twelve memories, identical across all six arms                |
| Stimuli      | Twenty-four, one per cycle, identical across all six arms                      |
| Arms         | The six canonical arms. The two reference arms are configured off, not removed |
| Budget       | Calibrated from the seed set; written by `make pilot-calibrate`                |
| Checkpoints  | Cycles 0, 12, and 24                                                           |
| Model access | Fixture mode by default; no credentials required for any command               |

The five phases of the deck are orientation (1–5), distractor flood (6–10),
contradiction pressure (11–15), recovery cues (16–20), identity stress (21–23), and one
autobiography (24). Contradictions are always presented as claims, recordings, or
damaged evidence; the deck never narrates that a canonical fact is false.

## The lifecycle of a protocol

```
make pilot-validate    # do the five files agree, and has any frozen one been edited?
make pilot-calibrate   # count the seed world, write the budget it implies
make pilot-freeze      # digest each file and mark it frozen
make pilot-local-run   # twenty-four fixture cycles, exported with checksums
```

The order is enforced by the commands, not by this document. `freeze` refuses an
uncalibrated protocol, because a budget frozen before it was measured is a number
nobody derived from anything. A run refuses a protocol that is not frozen, has been
edited since freezing, or has no budget.

Each file moves through `draft` → `frozen` → `retired`. The digest covers the parsed
content rather than the file bytes, so reflowing a YAML block is not a modification and
changing a word of a stimulus is.

## What only the protocol knows

Only a stimulus's `text` and a seed's `text` ever reach a model. Everything else in
those files is scoring apparatus and is asserted absent from every rendered request by
`tests/unit/test_pilot_blindness.py`:

- fact identifiers, categories, importances, and entities on a seed
- `relevant_fact_ids`, `phase`, `reliability`, and `evaluator_notes` on a stimulus
- the truth ledger, in full
- any other arm's memories, and any later cycle's stimulus

Memories are shown under per-request labels (`m1`, `m2`) rather than real identifiers,
because a real identifier reads `mem_arm_fifo_000007` and would name the mechanism in
every prompt. That is [ADR-010](adr/010-opaque-memory-labels-in-prompts.md), and the
pilot inherits it unchanged.

## Where the pilot differs from the production design

These are deliberate reductions for Pilot V1. Each one is a decision recorded here, not
a gap someone forgot.

### One cycle engine and immutable snapshots, not an event ledger

[ADR-002](adr/002-event-ledger-and-projections.md) (event ledger plus projections) and
[ADR-003](adr/003-step-functions-standard-workflow.md) (Step Functions with a six-arm
inline map) are **deferred, not withdrawn**. Pilot V1 runs one in-process engine and
stores one snapshot per arm-cycle. See
[ADR-008-pilot](adr/ADR-008-pilot-snapshot-architecture.md) for the full argument and
for what would make us build the distributed version.

### The citation auditor is not called on the cycle path

The production design audits every claimed citation with a model before it may move a
policy statistic. The pilot runs in `citation_mode: claimed_validated`: a writer's
claims are accepted after **structural** validation only —

1. the memory exists in this arm,
2. it is currently active,
3. duplicates are collapsed to one,

— and nothing else. A citation accepted this way records
`auditor_version: claimed.validated-v1`, so nothing downstream can mistake it for an
audited one.

Two reasons. An auditor call per arm per cycle is 144 extra model calls on a run whose
whole point is to see whether the mechanisms separate at all; and the auditor's value
is in distinguishing a _claimed_ citation from a _used_ one, which is a second-order
question the pilot is not yet asking. `CitationMode.AUDITED` exists, the gateway
implements the auditor in full, and the engine refuses to run in that mode rather than
silently downgrading.

### Checkpoint interviews, not continuous evaluation

The production design evaluates every cycle. The pilot interviews at cycles 0, 12, and
24 and makes no evaluator call at all. The per-cycle allowance is exactly six writer
calls and at most two Dreamer summaries; interviews spend from a separate checkpoint
allowance so the normal cycle ceiling stays what the protocol declares.

An interview never enters memory and never moves a citation statistic. That is
enforced structurally — checkpoint records are carried beside the run rather than
inside an arm's state — and asserted by test.

### No persistence, no API, no deployment

The engine holds the run in memory. `make pilot-local-run` writes a directory and a
`checksums.sha256` that `sha256sum -c` verifies. There is no DynamoDB, no S3, no
handler, and no CDK resource in this phase.

## The Dreamer

The Dreamer is the summarising arm. It is not a separate mechanism and not a separate
model role: `arm_summary` plans a compression, the existing `MemorySummarizer` writes
the words for exactly that plan, and the arm charges the result against the same budget
as any other memory. That is [ADR-009](adr/009-two-stage-compression.md) unchanged.

Its lineage is the domain's existing summary lineage. A summary is a `Memory` of kind
`summary` naming at least two `parent_memory_ids`; the domain refuses to construct one
that names fewer, and every source is retired as `compressed` with a
`MemoryLineageEdge` pointing at the summary. The pilot's protocol supplies the
parameters — target size, safety margin, minimum sources, fallback rule — and adds
nothing to the mechanism.

The `dreamer` block in `pilot-v1.yaml` maps one-to-one onto the domain's
`SummarizationConfig`. There is no second implementation to keep in step.

## What a run is allowed to spend

|                 | Per normal cycle | Per checkpoint |
| --------------- | ---------------- | -------------- |
| Writer          | 6                | 0              |
| Dreamer summary | up to 2          | 0              |
| Interviewer     | 0                | 6              |
| Evaluator       | 0                | 0              |
| Auditor         | 0                | 0              |

Checked **before** each call, so a protocol that would overspend stops rather than
discovering it in a bill. A whole run is additionally capped at
`max_model_calls_per_run`. Arms are generated with bounded concurrency
(`max_parallel_model_calls`, default 3); the order results are _stored_ in is the
configured arm order regardless of which call returned first.

## The one rule about advancing

Six arms advance together or none does. A cycle is generated, rebalanced, and turned
into six snapshot candidates while the run's state is untouched. Only after all six
succeed and the cross-arm checks pass is the run advanced, in a single assignment. An
arm that fails leaves all six states exactly as they were, because five arms on cycle
12 and one on cycle 11 is no longer the same experiment.

## Fixture mode

Everything the pilot produces locally is marked simulated in three independent places:
the `[simulated]` prefix inside the generated text, `simulated: true` on every call's
metadata and every snapshot, and a notice in the exported `run-manifest.json`. A run
marked `canonical` refuses to start on simulated models, and a production runtime
refuses a fixture gateway at all
([ADR-011](adr/011-exact-token-counts-in-production.md) and the gateway's own
configuration guard).

Nobody should ever have to recognise a fabricated run by noticing the model name.
