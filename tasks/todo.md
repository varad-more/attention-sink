# Pilot Phase 7 — AWS adapters, CDK, staging deployment, real Bedrock smoke testing

## Plan

- [x] Survey the existing ports, SQLite adapter, services, API, CDK app
- [x] STEP 1 — credential preflight, as a command rather than a checklist
- [x] STEP 2 — `DynamoRepository`, one table, one partition per run, no scans
- [x] STEP 3 — the atomic six-arm commit, one `TransactWriteItems`
- [x] STEP 4 — `S3ExportStorage` behind an `ExportStorage` seam; local retained
- [x] STEP 5 — three Lambda handlers, thin over the existing services
- [x] STEP 6 — `PilotStack` in three environment configurations
- [x] STEP 7 — least-privilege IAM, written out rather than granted
- [x] STEP 8 — one schedule, disabled, with operator commands
- [x] STEP 9 — CloudFront over a private bucket with OAC, SPA fallback, CSP
- [x] STEP 10 — structured logging, four metric filters, nine alarms
- [x] STEP 11 — CDK unit tests and assertions, 38 of them, before deploying
- [x] STEP 12 — deployed to staging
- [x] STEP 13 — `run_aws_staging` created from the locally validated protocol
- [x] STEP 14 — real Bedrock smoke tests and a real six-arm cycle
- [x] STEP 15 — failure tests, four in staging and six under moto
- [x] STEP 16 — `docs/pilot/aws-staging-report.md`
- [x] `make verify` green; local mode still passes all 16 checks

## Review

### What was built

`packages/aws` is the only package above the model gateway that imports an AWS SDK:
the DynamoDB repository, the S3 export storage, the structured logger, the
cycle-completed event, the deployment settings, the composition root, and the three
handlers. `infrastructure/cdk` gained `PilotStack` and three environment
configurations. Nothing above the adapter line changed to fit AWS.

### Four decisions worth recording

**The lock lives on the run's own item.** A separate lock item would need its own
transaction entry and its own race; as attributes of `RUN#{id} / META`, "the run is
where I left it" and "the lock is still mine" are one condition on one item.

**Immutability is a write condition.** SQLite refuses to rewrite a snapshot with a
trigger. DynamoDB has no triggers, so a snapshot is written with
`attribute_not_exists` and a rewrite fails rather than replacing a committed record.

**The analysis handler claims a cycle before it works and releases the claim if it
fails.** Claiming afterwards would let two deliveries both analyse; claiming without
releasing would let one crash make a cycle permanently unanalysed. Both were exercised
for real in staging.

**The counter is declared, never fallen back to.** Bedrock `CountTokens` is
unavailable for every model this account can reach, and the engine counts on every
cycle. ADR-012 makes `TOKEN_COUNT_SOURCE` a declaration recorded in the manifest, and
refuses an approximate one for a canonical run.

### Three defects, and where each came from

**Bedrock model identifiers do not fit in a `Version`.** Found by the first deployed
analysis. `amazon.nova-micro-v1:0` carries a colon; fixture mode returns
`fixture-evaluator-v1`, which is already version-shaped, so every local run passed.
Fixed with `version_token`, which transliterates rather than hashes.

**The export and the API labelled real generations as simulated fixtures.** Found by
reading the first deployed export. `EXPORT_LABELS` and `ApiEnvelope.simulated` were
constants. Both are now derived from the run, as four independent labels.

**A redaction pass that could never fire.** Found while closing a coverage gap:
`telemetry.py` filtered field _names_ for secrets after already filtering by a closed
allowlist that excluded all of them. Deleted. The allowlist is the whole guarantee,
and a second weaker rule beside it is the one somebody would come to rely on.

### What is deliberately not here

No canonical run, no Step Functions, no event ledger, no WebSocket, no SNS topic
behind the alarms, and no production deployment. Each is listed with its reason in
`docs/implementation-status.md`.

### Cleaning up

Every resource this phase created, and how to remove it, is
`docs/pilot/aws-staging-teardown.md`. `make aws-destroy AWS_ENV=staging` removes all
of it. The stack currently sits disarmed: execution disabled, schedule disabled.
