# ADR-007: Python backend, TypeScript infrastructure, React frontend

Status: accepted, 2026-08-29.

## Context

Three concerns with genuinely different demands: an experimental engine whose
correctness has to be arguable on paper, cloud infrastructure that has to be
reviewable and testable before it is deployed, and a public interface that has to
make divergence between six agents legible.

A single-language monorepo would be simpler to operate. It would also mean either
writing the domain engine in TypeScript, away from the analysis ecosystem the
results will eventually need, or writing infrastructure in Python's CDK bindings,
which trail the TypeScript ones in both maturity and documentation.

## Decision

- **Backend: Python 3.12.** Pydantic v2 for schema validation at every boundary,
  Strands Agents SDK for schema-validated model calls, boto3 for AWS, Powertools
  where it materially improves logging, tracing, or idempotency. Ruff and mypy in
  strict mode. pytest with Hypothesis for the properties that matter more than
  examples: budget invariants, determinism, total orderings.
- **Infrastructure: AWS CDK v2 in TypeScript**, the first-class CDK language, with
  `aws-cdk-lib/assertions` tests that run in CI with no credentials.
- **Frontend: TypeScript, React, Vite**, with a typed server-state library, Vitest,
  React Testing Library, and accessible SVG visualisations rather than a charting
  library, because the interesting views here are custom.

One npm workspace covers `apps/web` and `infrastructure/cdk`, sharing ESLint,
Prettier, and a strict `tsconfig.base.json`. One `uv` lockfile covers Python. One
Makefile runs both, and CI runs the same targets.

The boundary that matters is not between languages but between layers:
`packages/domain` and `packages/policies` import nothing but the standard library
and Pydantic, and a test enforces it.

## Consequences

- Contributors need both toolchains. `make bootstrap` installs both in one command.
- Types stop at the language boundary. API contracts are therefore defined by
  Pydantic models, and the frontend's types must be derived from them rather than
  written twice; keeping those in step is a real cost that later phases must pay
  deliberately.
- The experimental engine can be tested, replayed, and reasoned about with no AWS
  and no browser, which is the property everything else is arranged to protect.

## Revisit when

The frontend needs to share non-trivial logic with the backend, or a generated
client makes hand-written API types indefensible.
