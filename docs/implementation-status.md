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
  type-aware rules, Prettier, Vitest, CDK assertions, `package-lock.json`.
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

### Carried ahead of schedule: domain and policy packages

`packages/domain` and `packages/policies` were implemented before the Phase 1 brief
narrowed scope, and are preserved rather than discarded. They are pure, dependency
free, and covered by the import-boundary test, so they neither weaken nor extend the
Phase 1 surface. They are isolated in their own commit so the Phase 1 boundary can
be recovered with a single `git reset`.

They are **not** reviewed as part of Phase 1 and are **not** claimed as complete.
They carry no dedicated unit or property tests yet, and the coverage report says so
plainly: 0% across all fourteen modules, holding the repository total at 21%. Writing
those tests is the first task of Phase 2, and the number is left visible rather than
excluded from the report so that it cannot be quietly forgotten.

## Phase 2 - Domain engine and policies

**Status: partially landed, unreviewed.** See above. Outstanding: unit and property
test suites for every policy, the citation-audit contract, and the cycle-commit
transaction boundary.

## Phase 3 and later

Not started: persistence and event ledger, Step Functions orchestration, the model
gateway's Bedrock adapter, prompts, metrics, public API, WebSocket, forks,
moderation, CDK resources, deployment, and the experiment views in the web client.
