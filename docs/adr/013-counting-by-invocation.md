# 13. The writer model counts its own input, through an invocation capped at one token

Status: Accepted, 2026-08-30. Amends [ADR-011](011-exact-token-counts-in-production.md)
and supersedes the canonical-run consequence of
[ADR-012](012-approximate-token-counts-in-staging.md).

## Context

ADR-011 makes the writer model's own tokenisation the unit the active-memory budget is
denominated in, with no fallback. ADR-012 recorded that Bedrock's `CountTokens`
operation is unavailable for every model this account and Region can reach, allowed a
deployment to _declare_ the approximate counter instead, and left the canonical
twenty-four-cycle run blocked: `EXACT_TOKEN_COUNT_SOURCES` held one name, and nothing
could produce it.

Phase 8 re-probed before accepting that blocker. `CountTokens` still answers
`ValidationException: The provided model doesn't support counting tokens` for every
Nova model, for `anthropic.claude-3-haiku`, and for every Anthropic inference profile
the account can reach, current models included. The operation is not coming.

What ADR-011 actually requires, though, is the _number_, not the operation. Every
Bedrock invocation reports `usage.inputTokens` — the provider's own count of exactly
the input it was given, produced by exactly the tokeniser the writer model reads with.
A `Converse` request whose output is capped at a single token asks for that number and
almost nothing else.

Measured on `amazon.nova-micro-v1:0`: `"hello world"` counts 2, the same text repeated
two hundred times counts 400, and adding a system turn adds its own tokens. Repeated
calls agree exactly. It is the same measurement `CountTokens` would have returned, by a
different route.

## Decision

Add a second exact counter. `ConverseTokenCounter` subclasses `BedrockTokenCounter`
and overrides two things: the operation it calls, and where in the answer the total
is. Everything else — the content-hash cache, the retry policy, the metadata record,
the refusal to estimate when a call fails — is shared code, because those properties
should not differ between two counters that produce the same number.

`TokenCountSource` gains `CONVERSE`, `EXACT_TOKEN_COUNT_SOURCES` gains
`bedrock_converse_usage`, and a canonical run may be denominated in it.

**Counting is now a billed call, and is budgeted like one.** `ModelCallLimits` gains
`token_count_calls_per_cycle`, the engine claims an allowance before it counts, and it
records the metadata afterwards. A cache hit reports zero tokens rather than the
tokens it did not spend, so a run's tally describes the bill and not the arithmetic.
The pilot counts once per arm per cycle — one candidate memory — so the ceiling is
six, and a counting loop stops instead of spending.

**A run records the counter it used, not the one the protocol declares.**
`counter_identity(gateway)` reads the version off the built gateway, and
`from_bundle` takes both the version and the source name as overrides. This is what
keeps a frozen canonical protocol honest when the same files are replayed locally
against fixture models: the budget number is the frozen one, the counts are
heuristic, and the run says so in both fields.

## Consequences

The canonical run is unblocked, denominated in the writer model's own tokens, and
`bedrock-converse-usage-v1` says which of the two exact routes produced them. Two runs
counted by the two routes are comparable in a way a heuristic run never was — but the
version is still distinct, because "the same number" is a measurement we have checked
and not an identity we can assert.

A count costs one input-priced request plus one generated token. For twenty-four
cycles that is a hundred and forty-four extra invocations, which the cost report
carries as its own line rather than folding into the writers.

The Phase 7 staging run remains denominated in `heuristic-v1` and is not comparable
with the canonical run. Nothing is recomputed backwards: two runs counted differently
are two experiments, which is exactly what ADR-012 said.

ADR-012's allowance survives for what it was written for — a deployment with no exact
counter at all may still declare the approximate one and may still never be canonical.
Its statement that the canonical run is blocked until `CountTokens` covers a reachable
model is superseded by this record.

## Revisit when

`CountTokens` covers a model the experiment uses, and the two routes can be compared
directly on the same text. If they agree, `BEDROCK_COUNTER_VERSION` becomes the
preferred name and this counter becomes the fallback nobody needs. If they disagree,
the disagreement is a finding worth publishing.
