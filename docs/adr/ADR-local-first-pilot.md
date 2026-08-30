# ADR-local-first-pilot: complete the application locally before requiring AWS

- **Status:** Accepted
- **Date:** 2026-08-29
- **Supersedes:** nothing
- **Amends:** the implementation _order_ of ADR-002, ADR-003, ADR-011. None of their
  decisions is reversed.

Deliberately not numbered into the `NNN-` sequence. This record is about the order in
which the existing decisions get implemented, not a new architectural decision, and
giving it a sequence number would imply it sits alongside them.

## Context

Phases 1–3 produced the domain kernel, six memory policies, the model gateway, the
Bedrock and Strands adapters, prompt templates, and structured output schemas. All of
it works. None of it has ever been run as an experiment, because nothing sequenced a
cycle.

The original plan put AWS infrastructure next: DynamoDB persistence, Step Functions
orchestration, Lambda handlers, and a canonical Bedrock run. That ordering has a
specific failure mode. Every bug in cycle sequencing, atomic six-arm commits,
idempotency, metric definitions, and export schemas would first surface inside a
distributed workflow, against a paid model, with a state machine between the failure
and the reader. The cheapest bugs to fix would be discovered in the most expensive
place to find them.

There is also a scientific cost. A canonical run should be the first time the
experiment is run _end to end_, not the first time the code paths are exercised. If
the debugging happens during the canonical run, the canonical run is a debugging
session.

## Decision

**Complete the entire application locally before requiring any AWS credential.**

Phases 4–6 build a fully functioning pilot on a laptop: six arms, twenty-four cycles,
local persistence, atomic six-arm commits, checkpoint interviews, all four primary
metrics, the Graveyard, the Timeline, a public read API, the React frontend, dataset
export, and complete end-to-end tests. Phases 7–8 replace the infrastructure adapters
with AWS implementations and perform deployment, real Bedrock validation, the
canonical run, and public release.

Three constraints make that more than a scheduling preference.

**One implementation, two adapter sets.** Local and AWS execution share the domain,
the policies, the application services, protocol loading, prompt construction, the
gateway and repository interfaces, cycle orchestration, snapshot schemas, metric
definitions, API response schemas, and the frontend. Only adapters differ. There is
no separate simplified local path — a local green run has to be evidence about the
code that will run in production, or it is evidence about nothing.

**A hard credential boundary.** Phases 4–6 make no AWS call of any kind and require
no credential. Enforced by two existing properties rather than by discipline: no
`boto3` client is constructed at import time, and `MODEL_MODE` defaults to `fixture`
while `AS_RUNTIME_MODE=production` with `MODEL_MODE=fixture` is a configuration error.
There is no fallback path from a failed real call to a fixture response.

**Local runs are marked, structurally, as not being results.** `run_kind` is
`local_fixture` on the configuration _and on every snapshot_; `simulated` is true on
every snapshot; the export manifest carries a `SIMULATED - LOCAL - NON-CANONICAL`
notice; the generated text itself is prefixed. A fixture run's behavioural
differences between arms are never presented as findings.

## The protocol lifecycle this implies

`DRAFT → LOCAL_VALIDATED → AWS_CALIBRATED → FROZEN → RETIRED`

The pilot protocol reaches `LOCAL_VALIDATED` in Phase 4 and stops. It is
cross-checked, digested, manifested, and runnable against fixture models. It may be
changed only by returning it to `DRAFT`, which clears every digest.

**It must not be frozen in this phase**, and `promote_documents` refuses to write
`FROZEN` or `AWS_CALIBRATED` rather than leaving that to a reviewer. The reason is
ADR-011: a budget is denominated in the tokens of the counter that measures it, and
the counter available locally is a deterministic heuristic, not the production
model's tokeniser. A protocol frozen around that budget would make the canonical
experiment a measurement of the fixture counter. The budget is re-derived against
Bedrock `CountTokens` at Phase 8, which is the first moment freezing means anything.

Everything the local budget produces is labelled `PROVISIONAL_LOCAL_APPROXIMATION`,
including the calibration document itself.

## Consequences

**Good.** Every application bug is found where it is cheap. The canonical run is the
first _experiment_, not the first execution. Contributors can run the whole pilot with
no AWS account. The adapter line becomes load-bearing and is tested as such, rather
than being an aspiration nobody exercised.

**Bad.** Nothing about real model behaviour is learned until Phase 7. Fixture
generations are structurally plausible and semantically flat, so behaviour that
depends on a model actually reasoning — whether a summarising arm's compressions
retain the facts that matter — cannot be observed at all. Two arms whose fixture
trajectories are identical may diverge sharply on a real model; the local run will not
warn about it.

**Also bad.** The provisional budget will almost certainly be wrong in absolute terms.
The local counter and the production tokeniser will not agree, so Phase 8 calibration
will change the number and, with it, the cycle at which the budget starts binding. The
_shape_ of the experiment transfers; the exact pressure curve does not.

**Accepted risk.** Writing SQLite adapters in Phase 5 that DynamoDB adapters replace in
Phase 7 is duplicated work. It is bounded — two implementations of one repository
interface — and it buys a working application before the first bill, plus a second
implementation that keeps the repository interface honest about what it actually
requires.

## Revisit when

- Phase 7 shows the adapter line leaking: a DynamoDB adapter that cannot satisfy the
  repository interface without changing application code means the interface was
  drawn against SQLite rather than against the domain.
- Phase 8 calibration moves the budget far enough that the pressure curve stops
  resembling the local one — the pilot may then need re-piloting rather than
  re-calibrating.
- Fixture generations stop being able to exercise a code path we need tested, at
  which point the fixture invoker needs enriching rather than the boundary relaxing.

## Related

- ADR-001 application-level memory — unchanged
- ADR-002 event ledger and projections — deferred, not withdrawn
- ADR-003 Step Functions Standard workflow — deferred, not withdrawn
- ADR-004 policy-blind writer and evaluator — unchanged and enforced locally
- ADR-006 model identifiers from configuration — the reason fixture mode needs no
  credential
- ADR-008-pilot snapshot architecture — the storage model this phase implements
- ADR-011 exact token counts in production — the reason this protocol may not freeze
