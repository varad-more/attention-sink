# Implementation status

Kept deliberately honest. A phase is marked complete only when its acceptance
commands have been executed and passed, and anything not built is listed as not
built rather than omitted.

Last updated: 2026-08-29.

## Phase 1 - Repository foundation, tooling, and local developer workflow

**Status: complete.**

Delivered:

- Monorepo layout with a Python workspace (`packages/`), a TypeScript workspace
  (`apps/web`, `infrastructure/cdk`), and a single task runner (`Makefile`).
- Python 3.12 toolchain: `uv` lockfile, Ruff format and lint, mypy in strict mode,
  pytest with `unit` / `property` / `integration` / `e2e` markers derived from
  directory, Hypothesis, and branch coverage.
- Import boundaries enforced by test, not by convention: `packages/domain` may
  import only itself; `packages/policies` may import only the domain; neither may
  import `boto3`, `botocore`, Strands, Powertools, CDK, or moto.
- TypeScript workspace: strict `tsconfig.base.json`, ESLint flat config with
  type-aware rules, Prettier, Vitest, CDK assertions, `package-lock.json`. Vite 8,
  Vitest 4, and CDK 2.267 are pinned deliberately: the versions the templates
  default to carry five advisories, one critical, in the dev server and test
  runner. `npm audit` reports zero on the committed lockfile, and Node 20.19+ or
  22.12+ is required as a result.
- `packages/protocol` - version identity (schema, protocol, application, git commit)
  available to every backend service.
- `packages/model_gateway` - runtime mode resolution and the local fixture adapter.
  Local mode needs no AWS credentials and marks output simulated; production mode
  refuses to start without a Region and all five model identifiers.
- Web shell with a permanent simulated-data banner in local mode.
- CDK app that synthesises with no credentials and declares no resources yet.
- Seven ADRs, a system-context and container view, `CONTRIBUTING.md`, `SECURITY.md`,
  `.env.example`, `.editorconfig`, and pre-commit hooks including `gitleaks`.
- CI running the same `make` targets developers run, plus secret scanning. No
  deployment.

Deliberately not delivered in this phase:

- No AWS resources are declared, and nothing is deployed.
- No API handlers exist. There are no mock production endpoints of any kind.
- The web client renders a shell only. There are no experiment views, because there
  is no run to render.
- Directories from the target layout that no phase has filled yet
  (`services/`, `experiments/`, most of `packages/`) are described in
  `docs/architecture/container-view.md` rather than committed as empty scaffolding.
  They are created by the phase that first puts something in them.

## Phase 2 - The pure domain model and six memory-rebalancing policies

**Status: complete.**

Delivered:

- `packages/domain` - a dependency-free memory kernel. `Memory`, `MemoryStatus`,
  `MemoryKind`, `MemoryLineageEdge`, `MemoryState`, `CitationClaim`,
  `VerifiedCitation`, `PolicyDecision`, `PolicyDecisionCode`, `CompressionPlan`,
  `RandomProvenance`, `CycleContext`, `CycleSnapshot`, `TokenBudget`,
  `RunConfiguration`, `ModelConfiguration`, `LedgerEvent`, `MetricEvidence`, and the
  identifier and version aliases. Nothing imports AWS, a model provider, or a network.
- `packages/policies` - FIFO, least-recently-cited, citation-weighted heavy hitter
  with a recency reserve, pinned origin, seeded random, and two-stage lossy
  summarisation, plus the `arm_full` and `arm_stateless` reference arms.
- The twelve shared invariants, enforced structurally rather than by convention.
  A retired memory cannot read as active, a pinned memory cannot be retired, a
  summary always names at least two parents, and a final decision over its budget
  cannot be constructed at all.
- Deterministic explanations assembled from templates in `attention_sink.domain.explain`.
  No decision is ever narrated by a model.
- `scripts/simulate_policy.py`, a command-line simulator that runs the production
  packages against a JSON fixture. Its output is labelled simulated and any summary
  it stands in for is prefixed `[simulated summary of N memories]`.
- `docs/memory-policies.md` - the exact algorithm and tie-breaker for every arm,
  the decision-code table, and the fixture format.
- ADR 008 (budget token accounting) and ADR 009 (two-stage compression).

Test and coverage position:

- 397 tests: unit, Hypothesis property, and integration.
- Property tests run 250 generated states per property per arm, asserting what no
  arm may ever do: exceed the budget, mutate its input, retire a pinned memory,
  leave a tie unbroken, disagree with itself across two runs, or fail in a way other
  than `UnsatisfiableBudgetError`.
- **100%** of `packages/policies`, **99%** of `packages/domain`, gated per package at
  95% in `make test`. Gated per package rather than in aggregate, so a well-covered
  package cannot hide an untested one behind a flattering total.

Deliberately not delivered in this phase:

- No persistence. `LedgerEvent` and `CycleSnapshot` are defined but nothing writes
  them; there is no DynamoDB or S3 adapter and no projection.
- No model calls. `packages/policies` cannot reach a model, and the orchestrator that
  would sit between the summarising arm's two stages does not exist yet.
- No citation auditor. `VerifiedCitation` records what an auditor concluded; nothing
  yet produces one.
- No prompts, metrics computation, API, or orchestration.

### Note on the Phase 1 carry-over

`packages/domain` and `packages/policies` first landed in Phase 1's commit
`0623286`, ahead of the brief that scoped them. That code modelled memory as an
immutable record plus a separate active-memory projection. The Phase 2 brief
specifies a different shape - one `Memory` record carrying its own status and citation
statistics, and a `rebalance(state, budget, context) -> PolicyDecision` contract - so
both packages were rebuilt rather than adapted. The two shapes disagree about where
mutable state lives, and a half-migration would have left both.

## Phase 3 and later

Not started: persistence and the event ledger, Step Functions orchestration, the
model gateway's Bedrock adapter, prompts, the citation auditor, metrics computation,
public API, WebSocket, forks, moderation, CDK resources, deployment, and the
experiment views in the web client.
