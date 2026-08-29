# 8. The active-memory budget is denominated in versioned budget tokens

Status: Accepted, 2026-08-29. Amended by [ADR-011](011-exact-token-counts-in-production.md),
which makes the writer model's own tokenisation the production unit and confines
`heuristic-v1` to isolated tests and local fixture mode.

## Context

Every arm operates under a fixed active-memory token budget, and the budget is the
only pressure that makes the arms diverge. So the unit it is measured in is an
experimental parameter, not an implementation detail.

The obvious choice is the generation model's own tokenisation. It has three problems.
It requires a tokeniser dependency inside the pure domain, which would break the
boundary that lets the mechanism be tested without a model provider. It is not stable:
a vendor may re-tokenise the same string differently between model versions, which
would silently change every historical budget. And it makes replay depend on a
third-party artefact that may not exist by the time anyone tries to reproduce a run.

We also considered counting characters, which is stable but charges nothing for the
structure of the text, and counting whitespace-delimited words, which under-charges
long tokens badly enough that a single URL costs the same as a short sentence.

## Decision

The budget is denominated in **budget tokens**: an explicit, versioned unit computed
by a `TokenCounter` implementation whose version is recorded in the run manifest and
on every `TokenBudget`.

The default implementation, `heuristic-v1`, splits on ASCII whitespace and charges
each word `max(1, ceil(len(word) / 4))`, with 0 for empty or whitespace-only text. It
is pure, dependency-free, monotone non-decreasing under concatenation, and identical
on every machine and Python build.

`Memory.token_count` is **stored**, not recomputed on read, so a historical budget
stays auditable even if a later run uses a different counter version.

All budget arithmetic is integer arithmetic. Comparisons use
`TokenBudget.is_satisfied_by`, and ceiling division is integer division, so no
float ever decides whether an arm is within budget.

## Consequences

Budget tokens are not model tokens and must never be described as though they were.
A published figure has to say "budget tokens, `heuristic-v1`", because a reader who
assumes model tokens will draw a wrong conclusion about how much context each arm
actually held.

What the experiment needs is not that the unit matches any vendor's, but that it is
applied identically to every arm in a run and recorded. Two runs using different
counter versions are different experiments, and the manifest says which was used.

The domain stays free of tokeniser dependencies, and the whole memory kernel remains
testable with no model provider, no network, and no credentials.

Because `token_count` is stored, a record whose text was edited after the fact would
carry a stale cost. `Memory.content_hash` is verified against the text on every load,
so such a record fails to load rather than passing as canonical.

## Revisit when

A published result depends on comparing budget tokens against a model's real context
consumption, or an arm's behaviour is shown to be an artefact of the heuristic rather
than of its mechanism. Either would justify a `TokenCounter` implementation backed by
a real tokeniser, introduced as a new counter version, in a new run — never applied
retroactively to a committed one.
