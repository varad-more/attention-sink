# 11. Production budgets are counted by the model; the heuristic stays for tests

Status: Accepted, 2026-08-29. Amends ADR-008. Amended in turn by
[ADR-012](012-approximate-token-counts-in-staging.md), which lets a deployment whose
Region offers no model supporting `CountTokens` _declare_ the approximate counter --
never fall back to it, and never for a canonical run.

## Context

ADR-008 denominates the active-memory budget in _budget tokens_ computed by a
versioned `TokenCounter`, with `heuristic-v1` as the implementation, and gives three
reasons: no tokeniser dependency inside the pure domain, stability against a vendor
re-tokenising, and replayability without a third-party artefact. It also names the
condition for revisiting: "a published result depends on comparing budget tokens
against a model's real context consumption."

Phase 3 reaches that condition. The gateway now sends the active memories to a model,
and the honest claim about an experiment in memory pressure is how much of the
model's context each arm actually held. `heuristic-v1` charges
`max(1, ceil(len(word) / 4))` per whitespace-delimited word, which is close enough to
order the arms consistently and not close enough to publish as a context size.

Bedrock exposes `CountTokens`, which reports exactly what a given model will charge
for a given request, with no tokeniser dependency of our own.

## Decision

A production run's budget is denominated in the writer model's own tokens, counted
through `bedrock-count-tokens-v1`. The counter counts a serialised block of active
memory, a complete two-turn request, and any candidate memory or summary, and caches
on model identifier plus content hash.

The counter version does not embed the model identifier: `Version` excludes the `:`
that model identifiers contain, and the identifier is already recorded in the run
manifest. The pair — this counter version and the manifest's writer model — is what
identifies the counting function. `BedrockTokenCounter.descriptor` renders both
together where one readable string is wanted.

`heuristic-v1` remains, and is now confined to two places: isolated tests, and local
fixture mode where no model is being called at all. It is reached by configuration,
never by fallback.

There is no fallback. A production process whose counter is unavailable raises and
stops. Nothing else in the gateway degrades quietly, and this is the one place where
degrading quietly would be invisible: every arm in that run would be measured against
a budget in a different unit from the one its manifest claims, and no downstream check
would notice.

The pure packages are unchanged. `packages/domain` still declares `TokenCounter` as a
protocol and still ships only the heuristic implementation; the Bedrock one lives in
the gateway, on the adapter side of the boundary.

## Consequences

A published figure can now say what it means: tokens of the writer model named in the
manifest. ADR-008's warning still applies to any run counted with `heuristic-v1`, and
the counter version on every `TokenBudget` says which was used.

Counting costs calls. The cache makes most of them free — every arm recounts memories
that have not changed since the cycle before, and the same block is counted by the
budget and again inside the request — but a production cycle now makes token-counting
calls it did not make before, and they are subject to the same throttling as any
other.

Two runs counted with different counter versions are different experiments. That was
already true under ADR-008 and is unchanged; what changes is that the difference is
now between a heuristic and a real tokenisation rather than between two heuristics.

A run cannot be recounted after the fact. `Memory.token_count` is stored, so a
historical budget stays auditable in the unit it was measured in.

## Revisit when

The writer model changes within a programme of runs and the token counts move enough
to make two runs' budgets incomparable. That is a manifest question rather than a code
one — the counts are recorded per run — but it would be worth a note in the analysis
rather than a discovery in it.
