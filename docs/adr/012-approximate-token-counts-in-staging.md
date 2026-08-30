# 12. A deployment may declare the approximate counter; a canonical run may not

Status: Accepted, 2026-08-30. Amends [ADR-011](011-exact-token-counts-in-production.md),
which stands for canonical execution. **Superseded in part** by
[ADR-013](013-counting-by-invocation.md): the allowance below stands, and its claim
that the canonical run is blocked until `CountTokens` covers a reachable model does
not — a second exact counter reaches the same tokeniser through `Converse`.

## Context

ADR-011 makes the writer model's own tokenisation the production unit for the
active-memory budget, with no fallback: a process whose counter is unavailable stops,
because silent degradation to an approximation would leave every arm in a run measured
against a budget in a different unit from the one its manifest claims.

Phase 7's staging deployment met a condition that decision did not anticipate. Bedrock
exposes token counting through the `CountTokens` API, and in the pilot's account and
Region **no available model supports it**. Every on-demand text model returns
`ValidationException: The provided model doesn't support counting tokens`, including
the Nova family the staging run uses, and the same is true of every Anthropic
inference profile the account can reach and of us-west-2, us-east-2, eu-central-1, and
ap-northeast-1.

The engine counts on every cycle -- the candidate memory's cost is measured before it
is admitted -- so with no counter there is no cycle at all. That turns "the exact
counter is unavailable" from a degraded run into no run, and Phase 7's job is to prove
a real six-arm cycle commits against real models.

We considered three answers. Waiting for `CountTokens` support makes the phase depend
on a vendor roadmap. Falling back automatically is precisely what ADR-011 forbids, and
for the right reason. Counting with a third-party tokeniser reintroduces the
dependency ADR-008 removed from the domain, and would still not be the model's own
count.

## Decision

The counter becomes a **declared** setting rather than an inferred one.
`TOKEN_COUNT_SOURCE` selects `bedrock` (the default) or `heuristic`, resolves into
`GatewaySettings.token_count_source`, and decides which counter the factory builds.

Three things make this an amendment rather than a reversal.

**It is chosen before the run, not after a failure.** There is still no fallback:
`BedrockTokenCounter` raises when `CountTokens` is unavailable and nothing catches it.
A run counted approximately is a run that said so before it started.

**It is recorded.** `PilotRunConfiguration.token_count_source` carries what was
actually used, `TokenBudget.counter_version` carries `heuristic-v1`, and both travel
into the run manifest and every export. A reader cannot mistake one unit for the
other.

**A canonical run may not use it.** `require_run_kind_consistent` refuses an
`AWS_CANONICAL` run whose `token_count_source` is not in
`EXACT_TOKEN_COUNT_SOURCES`, which today holds `bedrock_count_tokens` alone. The
refusal is a validator, not a convention, and it fires before a cycle can spend
anything.

The class was renamed from `FixtureTokenCounter` to `ApproximateTokenCounter` and
moved from `fixtures.py` to `tokens.py`, because it is now reachable outside fixture
mode and a name saying "fixture" would have become false.

## Consequences

The Phase 7 staging run is denominated in `heuristic-v1` budget tokens and says so.
Its arms are comparable with each other -- the same counter is applied identically to
all six, which is what ADR-008 says the experiment actually needs -- and are **not**
comparable with any later run counted the other way.

The canonical twenty-four-cycle run was blocked on this at Phase 7, and that blocker
was recorded in `docs/pilot/aws-staging-report.md` rather than worked around. Phase 8
removed it a different way: ADR-013 adds an exact counter that does not need
`CountTokens`, the protocol is calibrated against that counter, and only then frozen.
The paragraph below still describes what would happen to a deployment that had no
exact counter available at all.

An operator who sets `TOKEN_COUNT_SOURCE=heuristic` and then tries to create a
canonical run gets a refusal naming the unit. An operator who leaves it unset in a
Region without `CountTokens` gets a failed cycle with a Bedrock validation error,
which is the correct loud failure.

## Revisit when

Bedrock's `CountTokens` covers a model the experiment can use in the deployment
Region. At that point `TOKEN_COUNT_SOURCE` returns to its `bedrock` default, the
protocol is recalibrated against the real tokeniser, and this record becomes history
rather than a live allowance. It is never applied retroactively to a committed run:
two runs counted differently are two experiments.
