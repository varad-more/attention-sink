# 9. Compression is planned by the policy and written by a model, in two stages

Status: Accepted, 2026-08-29.

## Context

Five of the six canonical arms decide what to forget with arithmetic alone. The sixth
compresses: it replaces several memories with one shorter summary. That summary is
text, and only a model can write it.

This is the one place where the mechanism under study and the model being studied
would touch. If the model chose _what_ to compress, the arm's divergence would no
longer be attributable to a memory policy — it would be attributable to a model making
editorial judgements we cannot inspect, replay, or hold constant against the other
five arms.

There is also a budget problem. A summary occupies space. Its cost is not known until
it exists, but the decision that creates it has to be made before it exists.

We considered having the policy call a model directly, which would put a network
dependency inside the pure domain and make every policy test require a fixture or a
live provider. We considered charging summaries a fixed notional cost regardless of
their real size, which would let the summarising arm quietly exceed the budget every
other arm is held to.

## Decision

Compression is split into two stages, with the model strictly between them and
outside the policy package.

**Stage A, `rebalance`.** The policy selects source memories deterministically, in
oldest-first order, taking the shortest prefix that would reach the budget once
replaced by a summary at the configured ceiling, with the configured safety margin
intact. It returns a `CompressionPlan` — sources, the identifier the summary will
occupy, the token ceiling, the tokens freed, the margin — and retires nothing. The
decision is marked not final.

**Stage B, `finalize_compression`.** The caller obtains summary text from a model,
mints it as an ordinary `Memory`, and hands it back. The policy validates that it is
a summary, names exactly the planned sources, occupies the reserved identifier, and
costs no more than the ceiling. Then sources become `COMPRESSED`, a lineage edge is
written per source, and the summary is charged against the same budget as any other
memory.

`packages/policies` imports nothing that can call a model, and that is enforced by
`tests/unit/test_import_boundaries.py` rather than by convention.

## Consequences

The model chooses the words. It never chooses what is lost. Every arm's eviction
decision, including this one's, is made by code whose ordering is recorded.

A summary is an ordinary memory. It takes the next creation slot, is charged against
the same budget, and can itself be swept into a summary of summaries later — which is
what makes the compression hierarchical, and what makes the lineage worth recording.

The orchestrator becomes responsible for the round trip, which is why the policy
re-checks the budget in stage B instead of trusting the plan it is handed: a plan can
arrive replayed from the ledger or carried into a fork whose budget was tightened.

The arm can fail to find any legal compression — too few eligible sources, or a
ceiling too large to help. It then falls back to oldest-first eviction and says so
with `SUMMARY_FALLBACK_FIFO`, so a cycle where the arm was effectively a slow FIFO is
visible in the data rather than hidden in it. A run that would rather stop than
change mechanism can set `fifo_fallback_enabled = false` and get an
`UnsatisfiableBudgetError` instead.

An oversized summary is rejected as a `PolicyError`, not an `UnsatisfiableBudgetError`.
The budget is fine; the caller broke the plan's contract, and the error should name
the right culprit.

## Revisit when

The summarising arm's results turn out to depend more on the summary ceiling than on
the mechanism, or a protocol wants compression driven by semantic similarity rather
than age. Either would change stage A's selection rule. Stage B's contract — validate,
charge, record lineage — should survive that change unaltered.
