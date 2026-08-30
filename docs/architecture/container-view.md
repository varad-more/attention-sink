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

| Package         | Responsibility                                                                                                                                                  | Status            |
| --------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- |
| `domain`        | Memories, memory state, policy decisions, lineage, configuration, ledger                                                                                        | Phase 2, complete |
| `policies`      | The six mechanisms plus two reference arms, deterministic and pure                                                                                              | Phase 2, complete |
| `protocol`      | Schema, protocol, application, and commit version identity                                                                                                      | phase 1           |
| `model_gateway` | Runtime and model configuration, prompts, typed model protocols, Bedrock/Strands adapters, token counting, embeddings, local fixtures                           | phase 3, complete |
| `pilot`         | The pilot protocol, run configuration, cycle engine, model-call budget, snapshots, and the repository ports                                                     | Phase 4, complete |
| `persistence`   | The SQLite adapter and its migrations. Local only; nothing here reaches AWS                                                                                     | Phase 5, complete |
| `analysis`      | Origin Recall, Identity Drift, Graveyard, echo, contradictions, secondary metrics, dataset export                                                               | Phase 5, complete |
| `api`           | The read API. Read-only by construction; the same application the Lambda serves                                                                                 | Phase 5, complete |
| `aws`           | The DynamoDB repository, S3 export storage, structured logging, and the three Lambda handlers. The only package that imports an AWS SDK above the model gateway | Phase 7, complete |

### Lambda handlers

**Not a `services/` directory.** The original plan was thirteen handlers, one per
step of a Step Functions workflow. The pilot scope override replaced that with three
(`attention_sink.aws.run_cycle`, `.analysis`, `.read_api`), because a pilot cycle is
one transaction over six arms and splitting it across a state machine would mean six
partial commits to reconcile rather than one that either happens or does not
([ADR-008-pilot](../adr/ADR-008-pilot-snapshot-architecture.md)). They live in
`packages/aws/` so they are importable, type-checked, and covered like anything else;
a handler outside the package layout would be the one module nothing checked.

### `apps/web` - React client

Phase 1 ships the shell only: a title and a permanent banner stating that local-mode
data is simulated. The banner exists from the first commit so that no later view can
be added without it. Experiment views arrive when there is a run to render.

### `infrastructure/cdk` - AWS CDK v2

Phase 7 ships `PilotStack`: one table, three functions, an HTTP API, a schedule, a
rule, two dead-letter queues, two private buckets, and a CloudFront distribution, in
three environment configurations (`local`, `staging`, `production`). Every dangerous
default is off in all three -- execution disabled, schedule disabled, nothing
canonical -- and `infrastructure/cdk/test/pilot-stack.test.ts` asserts that against
the synthesised template rather than trusting the source.

The Python deployment package is built by `make aws-bundle` rather than by CDK, so
synthesis needs neither Docker nor a network.

### `experiments/` - the versioned experimental apparatus

`preregistration`, `protocols`, `seed-worlds`, `stimulus-decks`, `truth-ledgers`,
`predictions`, `ablations`. **Not created.** These are data, hashed and versioned;
changing any of them changes the protocol version.

## Runtime containers

Deployed in Phase 7, to a staging account: EventBridge Scheduler holding one disabled
schedule; the run-cycle Lambda it would invoke; one DynamoDB table holding snapshots
and projections in one partition per run; an EventBridge rule routing the
`CycleCompleted` event to the analysis Lambda; two SQS dead-letter queues; two private
S3 buckets; an API Gateway HTTP API in front of the read Lambda; CloudFront with
Origin Access Control in front of the frontend bucket; CloudWatch log groups, metric
filters, and alarms; and Amazon Bedrock behind the model gateway.

Two planned pieces are deliberately absent. **Step Functions**
([ADR-003](../adr/003-step-functions-standard-workflow.md)) is replaced by one
transactional commit; **the event ledger** ([ADR-002](../adr/002-event-ledger-and-projections.md))
is replaced by immutable per-cycle snapshots. Both decisions stand as records; their
implementation waits for a phase that needs them
([ADR-local-first-pilot](../adr/ADR-local-first-pilot.md)). There is no WebSocket API:
the exhibition polls.

## Local development

`make pilot-local-demo` runs the whole product -- database, run, API, and exhibition --
with no AWS account, no credentials, and no network calls to Bedrock. `make dev` runs
the web client alone. Local mode is labelled as simulated on screen, and
production mode refuses to start without a Region and all five model identifiers
([ADR-006](../adr/006-model-ids-from-configuration.md)).
