# Local-first architecture

The whole application is built and finished locally before any AWS credential is
required. Phases 4–6 produce a complete, working pilot on a laptop. Phases 7–8 replace
the infrastructure adapters underneath it and change nothing above them.

This is an ordering decision, not an architectural one. The architecture was already
ports-and-adapters; what this document fixes is _when_ each adapter gets written, and
what a run produced by the local ones is allowed to claim.

## What runs where

| Phase | Models  | Persistence | Export storage   | API                  | Scheduler             |
| ----- | ------- | ----------- | ---------------- | -------------------- | --------------------- |
| 4     | fixture | in-memory   | local filesystem | —                    | —                     |
| 5–6   | fixture | SQLite      | local filesystem | local HTTP           | local simulator       |
| 7–8   | Bedrock | DynamoDB    | S3               | API Gateway + Lambda | EventBridge Scheduler |

Phase 4 — this phase — has no persistence at all. The engine holds the run in memory,
hands back immutable snapshots, and never writes a file; a separate export module
decides what to do with what it produced. That is what makes the same engine usable
from a test, from a command line, and later from a Lambda handler without any of them
being the place the invariants live.

## What must not differ

Local and AWS execution share everything above the adapter line:

- domain models and the memory kernel
- all six memory policies
- application services and the cycle orchestration rules
- protocol loading and validation
- prompt construction and the policy-blindness guarantee
- model gateway interfaces
- repository interfaces
- snapshot schemas and their canonical hashing
- metric definitions
- API response schemas
- frontend code

Only the adapters differ. There is no second, simplified local implementation that
bypasses the real application logic — that would make a local green run evidence about
the local implementation and nothing else, which is precisely the failure this
ordering is meant to avoid.

The boundary is enforced by test rather than by convention.
`tests/unit/test_import_boundaries.py` fails if anything below the adapter line
imports a provider SDK, and fails if the pilot package reaches for one.

## The credential boundary

During Phases 4–6 nothing calls Bedrock, DynamoDB, S3, CloudFront, Lambda,
EventBridge, API Gateway, or CloudWatch; nothing runs the `aws` CLI or `cdk
bootstrap`/`cdk deploy`; nothing performs an account or Region lookup.

Two mechanisms hold that, neither of which is a promise in a document:

**No client is constructed at import time.** `boto3` is imported by
`model_gateway/factory.py`, but `boto3.Session().client(...)` runs inside
`_bedrock_client()`, which is reached only when `MODEL_MODE=bedrock`. Importing the
application constructs nothing and looks nothing up.

**Fixture mode is the default and production cannot fall back to it.**
`GatewaySettings.from_env` defaults `MODEL_MODE` to `fixture`, and the combination
`AS_RUNTIME_MODE=production` with `MODEL_MODE=fixture` is a `ConfigurationError`
rather than a warning. There is no code path from a failed Bedrock call to a fixture
response; a failure raises (ADR-006, ADR-011).

Local defaults:

```
MODEL_MODE=fixture
AS_RUNTIME_MODE=local
ALLOW_BEDROCK_CALLS=0        # Phase 5, once there is anything to gate
PERSISTENCE_MODE=sqlite      # Phase 5, once persistence exists
AWS_EC2_METADATA_DISABLED=true
```

The last three are listed for completeness. Phase 4 has no persistence layer to
configure and no second call path to gate, so nothing reads them yet.

## Local runs are not research results

A fixture run exercises the application. It says nothing about how a model remembers.

Every artefact a local run produces is marked in four places, so nobody has to
recognise a fabricated run by noticing a model name:

- `run_kind: local_fixture` on the run configuration **and on every individual
  snapshot**, because a snapshot travels — into an export, a fixture, a screenshot —
  and one that arrives without its provenance is one somebody will read as a result
- `simulated: true` on every snapshot
- a `SIMULATED - LOCAL - NON-CANONICAL` notice in the export manifest
- a `[simulated]` prefix inside the generated text itself

The token budget is marked too. `token_count_source: local_fixture_heuristic` says the
budget is denominated in the deterministic local counter's tokens rather than the
production model's — a `PROVISIONAL_LOCAL_APPROXIMATION`. See
`docs/pilot/local-token-calibration.md`.

Behavioural differences between arms in a fixture run are differences between the
_mechanisms_, driven by a deterministic text generator. They are the right shape and
they are not findings.

## Protocol lifecycle

`DRAFT → LOCAL_VALIDATED → AWS_CALIBRATED → FROZEN → RETIRED`

Phase 4 reaches `LOCAL_VALIDATED` and stops there. A `LOCAL_VALIDATED` protocol has
been cross-checked, digested, and manifested, and may run against fixture models. It
may be changed only by returning it to `DRAFT` (`make pilot-draft`), which clears every
digest, and re-validating.

It must not be frozen. The budget it carries was derived from the local counter;
freezing that would make the canonical experiment a measurement of the fixture
tokeniser. `AWS_CALIBRATED` and `FROZEN` are written in Phase 8, after the budget has
been re-derived against Bedrock `CountTokens`. `promote_documents` refuses to write
either of them.

## What is deferred, and what is not

Deferred to a later phase, with the ADR that will be reopened:

- full event sourcing and projections (ADR-002)
- Step Functions orchestration (ADR-003)
- DynamoDB, S3, Lambda, API Gateway, EventBridge
- causal forks, WebSockets, the Whisper Box, public write APIs, user accounts

Not deferred, and not weakened by running locally: every scientific invariant. Six
arms start identically, receive the same stimulus, share a writer configuration that
never learns the policy or the public character name, never see another arm, never see
a future stimulus, never see the truth ledger. Active memory stays within budget.
Evicted memories never silently return. Dreamer summaries cost the same budget as any
other memory and keep their lineage. Interviews never become memories. Snapshots are
immutable. Six arms commit together or none does. Raw chain-of-thought is never
requested or stored.

Those hold in fixture mode for the same reason they will hold in Bedrock mode: they
are enforced above the adapter line.
