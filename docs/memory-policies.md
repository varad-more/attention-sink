# Memory policies: exact algorithms and tie-breakers

Six mechanisms, one contract. Every arm receives the same seed memories, the same
ordered stimuli, the same writer model and prompts, the same inference settings, and
the same active-memory token budget. They differ **only** in which memories they
choose to keep. This document specifies that choice precisely enough to reimplement
or audit, because a divergence between arms is only evidence if the thing that
differed is known exactly.

Public-facing names for the arms are a presentation concern and appear nowhere in
this document, in any prompt, or in any policy input.

## The contract

```python
MemoryPolicy.rebalance(
    state: MemoryState,
    budget: TokenBudget,
    context: CycleContext,
) -> PolicyDecision
```

A policy is a pure function of `(state, budget, context)`. It reads no clock, opens
no socket, calls no model, and cannot see any other arm, any prediction, or any
metric. Randomness comes only from `context.run_random_seed`.

### What the decision contains

| Field                            | Meaning                                                               |
| -------------------------------- | --------------------------------------------------------------------- |
| `decision_code`                  | Which of the codes below applied                                      |
| `kept_memory_ids`                | The active set after the decision, in presentation order              |
| `retired_memory_ids`             | Everything leaving the active set, in the order it was chosen         |
| `retirements`                    | Each retirement with its resulting status and the code that caused it |
| `created_memories`               | Memories this decision adds — only ever a summary                     |
| `lineage_edges`                  | Parent → child links for anything created                             |
| `tokens_before` / `tokens_after` | Budget tokens held before and after                                   |
| `budget_tokens`                  | The ceiling in force, stored so the decision is self-contained        |
| `candidate_order`                | Every eligible memory with its rank index and rendered key            |
| `random_provenance`              | Digest, candidates, index, and selection for each draw                |
| `compression_plan`               | A summary this decision is _requesting_                               |
| `committed_compression`          | A summary this decision has _carried out_                             |
| `explanation`                    | Deterministic prose, assembled from templates, never generated        |
| `policy_version`                 | Bumped whenever behaviour changes                                     |

A decision is **final** when `compression_plan is None`. Only final decisions are
required to be within budget; the summarising arm legitimately passes through an
intermediate state where it has decided what to compress but the text does not exist.

## Invariants every arm honours

1. After a final decision, active tokens ≤ `budget.max_active_tokens`.
2. Memory identifiers are unique within a run.
3. A retired memory can never read as active. Enforced in `Memory` itself.
4. A pinned memory is never retired. Enforced in `Memory`, in `eligible_memories`,
   and again in `MemoryState.apply`.
5. A summary names at least two source memories.
6. Every source of a committed summary becomes `COMPRESSED` or `SUPERSEDED`.
7. No policy mutates its input. Every model is frozen; every transition revalidates.
8. Ties break on a canonical ordered tuple ending in `memory_id`.
9. Every policy configuration is a serialisable Pydantic model.
10. A memory born in the current cycle is never retired in that cycle. If the
    protected memories alone exceed the budget, the arm raises rather than breaking
    the rule.
11. When no legal decision exists, the policy raises `UnsatisfiableBudgetError`.
12. Every domain error carries run, arm, cycle, and policy version.

### Eligibility

A memory is eligible for retirement when it is `ACTIVE`, is **not** pinned, and was
**not** born in the cycle being decided. Everything else is protected. The
protected set is identical across arms, so it cannot be the source of a divergence.

### Why greedy selection is exact, not approximate

Every order-driven arm retires the shortest prefix of its ordering that reaches the
budget. This is not an approximation of some better packing. The ordering _is_ the
policy's entire notion of what is expendable, so retiring anything other than the
next memory in that order would be a different mechanism, not a better solution to
the same one.

## The orderings

Each ordering is declared once, as a list of named fields, and drives both the sort
and the `rank_key` recorded in `candidate_order`. One definition means the
provenance a reader sees cannot drift away from the comparison that actually ran.
All orderings are ascending — **most expendable first** — and all end in `memory_id`.

### `arm_fifo` — first in, first out

```
(birth_cycle, creation_sequence, memory_id)
```

`birth_cycle` alone is not total: several memories can be born in one cycle.
`creation_sequence` is the arm-local monotonic counter that resolves that, and it is
never reused — retiring the newest memory does not free its slot, because identifiers
are built from it.

### `arm_lru` — least recently verified-cited

```
(never_cited, last_verified_citation_cycle, birth_cycle, memory_id)
```

- `never_cited` is `0` for a memory with no verified citation and `1` otherwise, so
  never-cited memories sort ahead of every cited one.
- `last_verified_citation_cycle` renders as `-1` for never-cited memories. That is a
  sort sentinel only; it is never stored on a memory. "Never used" and "last used
  before the run began" are different facts, and the rank key says which applied.

Only citations with `source == WRITER` advance recency. Interview and evaluation
citations are recorded and then ignored, so probing an arm cannot change what it
goes on to remember.

### `arm_heavy` — citation-weighted retention

Each cycle, for **every** active memory, whether or not it was cited:

```
discounted_citation_score := decay * previous_score + verified_writer_citations_this_cycle
```

`decay` defaults to `0.90` and is frozen in `RunConfiguration.citation_decay`. A
decay applied only to cited memories would not be a decay.

Eviction minimises **retention density**:

```
retention_density = discounted_citation_score / max(token_count, 1)
```

Ordering:

```
(recency_reserved, retention_density, discounted_citation_score,
 last_verified_citation_cycle, birth_cycle, memory_id)
```

`recency_reserved` is `1` for the newest `recency_reserve` active memories by
`creation_sequence` (default `2`) and `0` otherwise. Leading with it means reserved
memories sort behind every unreserved one and are reached only once the unreserved
pool is exhausted — so "break the reserve, but only as far as the budget demands"
falls out of the ordering rather than needing a second pass with different rules.

Without the reserve the arm would evict every new memory on sight: a memory that has
not yet been shown to the writer scores zero by construction, not because it turned
out to be worthless.

Decision code is `EVICTED_LOWEST_RETENTION_DENSITY` normally, and
`HEAVY_HITTER_RESERVE_BROKEN` when any retired memory was reserved.

### `arm_sink` — pinned origin plus sliding window

Uses the `arm_fifo` ordering over everything except the pinned memory. The pin is
enforced twice: every arm refuses to retire a memory flagged `pinned`, and this arm
additionally protects the identifier in `PinnedOriginConfig.pinned_memory_id`. A run
may pin its origin by flagging the seed record, by naming it in configuration, or
both.

`RunConfiguration.validate_seed_memories` checks before cycle 0 that the pinned
memory exists, is a seed, and fits the budget on its own.

Because the pinned memory holds its tokens forever, this arm's usable window is
strictly smaller than `arm_fifo`'s. That cost is the mechanism, not a flaw in it.

### `arm_random` — seeded random baseline

For each decision index `i`, starting at 0, while the arm is over budget and
candidates remain:

```
candidates := sorted(remaining eligible memory ids)
digest     := SHA-256( 4:seed | 10:arm_id | 1:cycle | 1:i | <each candidate id> )
seed_int   := int(digest, 16)
index      := random.Random(seed_int).randrange(len(candidates))
selected   := candidates[index]
```

Every field in the digest payload is length-prefixed and joined with `|`, so the
payload is unambiguously decodable and no two different field lists can collide.

Candidates are sorted before selection, so the draw depends on _which_ memories were
eligible and not on the order the caller enumerated them. Each draw records its
digest, candidate list, selected index, and selected identifier — enough to replay
the choice without the original process.

A fresh `random.Random` is constructed per draw. The module-global generator is
never touched, so unrelated code in the same process cannot perturb an arm's history.

### `arm_summary` — lossy hierarchical summarisation

The only two-stage arm, and two-stage by necessity: the summary text does not exist
when the decision must be made, and its cost is not known until it does.

**Stage A — `rebalance`.** If already within budget, `NO_ACTION_WITHIN_BUDGET`.
Otherwise, walk the eligible memories in `arm_fifo` order, accumulating `freed`, and
return the first prefix where both hold:

```
size >= min_sources                                            (default 2)
tokens_before - freed + summary_target_token_limit
        <= budget.max_active_tokens - safety_margin_tokens
```

The result is a `CompressionPlan` naming the sources, the identifier the summary will
occupy, the token ceiling, the tokens freed, and the margin. Nothing is retired and
no content is invented. Decision code `COMPRESSION_PLANNED`; the decision is not
final.

If no prefix qualifies, the arm falls back to `arm_fifo` eviction and reports
`SUMMARY_FALLBACK_FIFO` — or raises `UnsatisfiableBudgetError` when
`fifo_fallback_enabled` is false, which is the honest choice for a protocol that
would rather stop than silently become a different mechanism.

**Stage B — `finalize_compression`.** The supplied summary is rejected unless it is
a `SUMMARY`, names exactly the planned sources, occupies the reserved identifier, and
costs no more than the ceiling. An oversized summary raises `PolicyError`, not
`UnsatisfiableBudgetError`: the budget is fine, the caller broke the plan's contract.

Then sources become `COMPRESSED`, one `COMPRESSED_INTO` lineage edge is written per
source, and the summary is charged against the same budget as any other memory. The
budget is re-checked rather than assumed — a plan can arrive replayed from the
ledger, or carried into a fork whose budget was tightened. If the arm is still over:
another plan is produced (`COMPRESSION_PLANNED`, with `committed_compression` naming
what was just carried out), or the FIFO fallback runs (`SUMMARY_FALLBACK_FIFO`).

A summary is an ordinary memory. It takes the next creation slot and can itself be
swept into a summary of summaries on a later cycle. That recursion is what makes the
compression hierarchical; the lineage keeps it auditable back to the original text.

**No model is called from the policy package.** The policy chooses what is lost and
how much space the result may take. Something outside chooses the words.

### Reference arms

`arm_full` retires nothing and raises if its budget cannot hold the run — a
full-memory reference that silently forgot would invalidate every comparison drawn
against it. `arm_stateless` retires everything not born in the current cycle, whether
or not the budget required it. Neither is part of the canonical six.

## Decision codes

| Code                               | Meaning                                   |
| ---------------------------------- | ----------------------------------------- |
| `no_action_within_budget`          | Already fitted; nothing retired           |
| `evicted_oldest`                   | `arm_fifo`                                |
| `evicted_least_recently_cited`     | `arm_lru`                                 |
| `evicted_lowest_retention_density` | `arm_heavy`, reserve intact               |
| `heavy_hitter_reserve_broken`      | `arm_heavy`, reserve invaded              |
| `evicted_outside_window`           | `arm_sink`                                |
| `evicted_random`                   | `arm_random`                              |
| `compression_planned`              | `arm_summary` stage A, or a further round |
| `compression_committed`            | `arm_summary` stage B, complete           |
| `summary_fallback_fifo`            | `arm_summary`, no legal compression       |
| `evicted_stateless`                | `arm_stateless`                           |
| `retained_all`                     | `arm_full`                                |

## The simulator

```
make simulate FIXTURE=datasets/fixtures/policy_simulator/divergence.json
uv run python scripts/simulate_policy.py <fixture> [--arm arm_fifo]... [--summary]
```

Runs the production packages, not a model of them, and prints each arm's
`PolicyDecision` as JSON. Output is labelled `"simulated": true`, and any summary it
stands in for is prefixed `[simulated summary of N memories]`. Exits non-zero if any
arm reports an unsatisfiable budget.

### Fixture format

```jsonc
{
  "run_id": "run_...",              // required
  "cycle": 12,                       // the cycle being decided
  "stimulus_id": "stim_012",
  "protocol_version": "...",
  "prompt_version": "...",
  "run_random_seed": "...",         // >= 8 characters
  "budget": { "max_active_tokens": 60, "counter_version": "heuristic-v1" },
  "policies": { ... },              // a PolicyConfiguration; omitted fields take defaults
  "arms": ["arm_fifo", ...],        // defaults to every arm
  "simulated_summary_tokens": 10,   // cost of the stand-in summary
  "memories": [                      // shared verbatim by every arm, in creation order
    {
      "text": "...",                // required
      "token_count": 16,            // required; budget tokens, not vendor tokens
      "memory_kind": "seed",        // seed | generated | summary | external
      "birth_cycle": 0,             // required
      "pinned": true,
      "source_stimulus_id": "stim_000",
      "citation_count": 3,
      "last_verified_citation_cycle": 9,
      "discounted_citation_score": 4.1
    }
  ]
}
```

Each arm builds its own memories from this one list, so identifiers differ per arm
(`mem_arm_fifo_000000`, `mem_arm_lru_000000`, …) while the content, order, costs, and
statistics are identical. That is the point: the shipped fixture puts six arms on the
same input and they reach four different active sets.

## Token accounting

Budget tokens are an explicit versioned experimental unit, not any vendor's
tokenisation — see [ADR 008](adr/008-budget-token-accounting.md). All budget
arithmetic is integer arithmetic. Floats appear only in citation scores, where a
fraction is part of the mechanism; ties there are still broken by the canonical
tuple, so no comparison depends on float equality.
