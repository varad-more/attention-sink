# Container view

The runtime and source containers, the dependency direction between them, and which
phase creates each. Directories not yet present in the repository are marked; they
are created by the phase that first puts something in them, rather than committed as
empty scaffolding.

## Dependency direction

Dependencies point downward, and never back up. This is enforced by
`tests/unit/test_import_boundaries.py`, not by review.

```
  apps/web            infrastructure/cdk
  (React client)      (CDK v2, TypeScript)
        │                     │
        │ HTTPS               │ deploys
        ▼                     ▼
  ┌──────────────────────────────────────────────┐
  │  services/*   one Lambda handler each        │
  │  thin adapters: parse, call, serialise       │
  └───────────────────────┬──────────────────────┘
                          │
  ┌───────────────────────▼──────────────────────┐
  │  adapter packages                            │
  │  model_gateway, persistence, observability   │
  │  implement protocols the domain declares     │
  └───────────────────────┬──────────────────────┘
                          │
  ┌───────────────────────▼──────────────────────┐
  │  pure packages                               │
  │  domain, policies, metrics, prompts, protocol│
  │  stdlib and Pydantic only. No boto3, no      │
  │  Strands, no Powertools, no CDK.             │
  └──────────────────────────────────────────────┘
```

## Source containers

### `packages/` - importable libraries

| Package         | Responsibility                                                                                                                        | Status            |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `domain`        | Memories, memory state, policy decisions, lineage, configuration, ledger                                                              | Phase 2, complete |
| `policies`      | The six mechanisms plus two reference arms, deterministic and pure                                                                    | Phase 2, complete |
| `protocol`      | Schema, protocol, application, and commit version identity                                                                            | phase 1           |
| `model_gateway` | Runtime and model configuration, prompts, typed model protocols, Bedrock/Strands adapters, token counting, embeddings, local fixtures | phase 3, complete |
| `persistence`   | DynamoDB and S3 adapters, event ledger, projections                                                                                   | not created       |
| `metrics`       | Scoring, evidence capture, evaluator and calculation versioning                                                                       | not created       |
| `prompts`       | Versioned, hashed prompt templates                                                                                                    | not created       |
| `observability` | Structured logging, tracing, metrics, idempotency                                                                                     | not created       |

### `services/` - one directory per Lambda handler

`prepare_cycle`, `generate_thought`, `audit_citations`, `plan_rebalance`,
`generate_summary`, `commit_arm_cycle`, `finalize_cycle`, `run_interviews`,
`calculate_metrics`, `public_api`, `websocket`, `fork_runner`,
`whisper_moderation`. **Not created.** Each will be a thin adapter over a pure
function so the pipeline can be exercised without Step Functions.

### `apps/web` - React client

Phase 1 ships the shell only: a title and a permanent banner stating that local-mode
data is simulated. The banner exists from the first commit so that no later view can
be added without it. Experiment views arrive when there is a run to render.

### `infrastructure/cdk` - AWS CDK v2

Phase 1 ships an app and a `FoundationStack` that declares no resources and
synthesises with no credentials. Resources arrive with the phase that needs them,
each scoped to the permissions it actually requires.

### `experiments/` - the versioned experimental apparatus

`preregistration`, `protocols`, `seed-worlds`, `stimulus-decks`, `truth-ledgers`,
`predictions`, `ablations`. **Not created.** These are data, hashed and versioned;
changing any of them changes the protocol version.

## Runtime containers

Planned, and not yet deployed: EventBridge Scheduler starting each cycle; a Step
Functions Standard Workflow per cycle with a six-arm inline map
([ADR-003](../adr/003-step-functions-standard-workflow.md)); Lambda functions per
step; DynamoDB holding the immutable event ledger and the mutable projections
separately ([ADR-002](../adr/002-event-ledger-and-projections.md)); S3 for exports
and large immutable artefacts; API Gateway HTTP and WebSocket APIs; CloudFront with
Origin Access Control in front of the private web bucket; an SQS dead-letter queue;
CloudWatch for logs, metrics, and alarms; and Amazon Bedrock behind the model
gateway.

## Local development

`make dev` runs the web client against fixtures with no AWS account, no credentials,
and no network calls to Bedrock. Local mode is labelled as simulated on screen, and
production mode refuses to start without a Region and all five model identifiers
([ADR-006](../adr/006-model-ids-from-configuration.md)).
