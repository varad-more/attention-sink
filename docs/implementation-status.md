# Implementation status

Kept deliberately honest. A phase is marked complete only when its acceptance
commands have been executed and passed, and anything not built is listed as not
built rather than omitted.

Last updated: 2026-08-30.

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
- `packages/model_gateway` - runtime mode resolution and an untyped local fixture
  adapter. Local mode needs no AWS credentials and marks output simulated;
  production mode refuses to start without a Region and all five model
  identifiers. The fixture adapter was replaced in Phase 3; see the note below.
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

## Phase 3 - The model gateway and schema-validated AI interactions

**Status: complete.**

Delivered:

- `packages/model_gateway` - seven typed protocols (`ThoughtWriter`,
  `CitationAuditor`, `MemorySummarizer`, `Interviewer`, `ClaimEvaluator`,
  `EmbeddingProvider`, `ExactTokenCounter`), one implementation set that runs over
  either a real provider or a deterministic local one, and a factory that chooses
  between them from validated configuration. No model interaction bypasses a protocol.
- One provider seam. `StructuredInvoker` is the only interface to a model; below it
  sits a single Bedrock class, `StrandsInvoker`, which builds a fresh stateless
  Strands agent per call with the model identifier always passed explicitly.
  Everything above it - prompt rendering, the blindness guard, response verification,
  retries, metadata - is provider-agnostic and runs unchanged in fixture mode.
- Six versioned prompt files shipped inside the package, each loaded with a SHA-256
  digest of its own bytes, plus a digest covering the whole set for the run manifest.
- Strict output schemas for all five text roles, with closed vocabularies for every
  categorical judgement, and adapter checks the schema cannot express: unknown labels
  rejected, one audit per claim, quoted evidence verified against the memory it cites,
  summary sources and ceiling enforced, every interview question answered.
- Policy blindness enforced mechanically. Memories are presented under per-request
  labels (ADR-010), so no real identifier - and therefore no arm name - reaches a
  prompt; `assert_policy_blind` then rejects arm identifiers, policy version strings,
  and mechanism vocabulary in every rendered request, on every attempt.
- Eight error codes, classified once, retried only where retrying can help, with
  bounded exponential backoff and full jitter. An unrecognised exception is re-raised
  rather than mislabelled.
- `CallMetadata` on every call, success or failure, carried on the exception when a
  call fails. Only the provider request identifier is read from a response; headers
  never are.
- Exact token counting through Bedrock `CountTokens`, cached on model identifier and
  content hash, with no fallback to the heuristic in production (ADR-011).
- Titan Text Embeddings V2 with configurable dimensions, model-side normalisation,
  and deduplication on `(model_id, input_hash)`.
- Fixture mode: a complete local cycle with no AWS account, reproducible, and marked
  simulated in both text and metadata.
- `docs/model-gateway.md`, ADR-010 (opaque memory labels) and ADR-011 (exact token
  counts, amending ADR-008).

Test and coverage position:

- 606 tests: unit, Hypothesis property, and integration. 603 run; the three Bedrock
  contract tests are skipped unless `AS_BEDROCK_CONTRACT_TESTS=1`.
- `tests/integration/test_fixture_cycle.py` drives one complete cycle through the
  Phase 2 kernel and the Phase 3 gateway together: write, audit, fold citations into
  the arm's statistics, plan a compression, write the summary, commit it, then
  interview, judge, and embed the result.
- **99%** of `packages/model_gateway`, gated at 95% in `make test` alongside the
  domain and policy packages. The uncovered lines are the three inside
  `StrandsInvoker.invoke` that actually reach Bedrock; the agent construction and the
  response unpacking around them are split out and tested.

Deliberately not delivered in this phase:

- No orchestration. Nothing sequences a cycle; the integration test wires the two
  halves together by hand, and that wiring is Phase 4's subject.
- No persistence. Nothing writes a `LedgerEvent`, a `CycleSnapshot`, or an
  `EmbeddingRecord`; the gateway's caches are per process and vanish with it.
- No no-op or failure implementations of the protocols. The brief permits them "only
  where explicitly useful", and nothing in this phase needed one: tests define their
  own doubles, and a shipped always-failing adapter would be a thing production could
  accidentally be configured with.
- No API, metrics computation, CDK resources, or web client work.

### Note on the Phase 1 fixture adapter

`FixtureModelGateway` served canned JSON responses keyed by task and string. It was
removed in this phase along with `datasets/fixtures/model_responses/`. It answered
requests no typed protocol had shaped and returned values no schema had validated,
which is exactly what acceptance criterion 1 - no model interaction bypasses a typed
gateway - forbids. Fixture responses are now produced by `FixtureInvoker` behind the
same adapters production uses.

## Pilot Phase 4 - The canonical pilot protocol and a complete local six-arm cycle engine

**Status: complete.**

Run under the Pilot Scope Override, which takes priority over conflicting requirements
in the original production-scale brief. What it narrows, and why, is
`docs/pilot-scope.md`; the architectural half is
`docs/adr/ADR-008-pilot-snapshot-architecture.md`.

### Audit of Phases 1-3 against the override

Performed before anything was written. Six of the seven named incompatibilities were
already satisfied by the existing implementation and nothing was rebuilt or deleted:

- **Policy labels in prompts.** Already prevented. Memories are presented under
  per-request labels (ADR-010) and `assert_policy_blind` runs on the system turn at
  render and on the data turn on every attempt.
- **Framework-managed memory.** Already prevented. `StrandsInvoker.build_agent`
  constructs a fresh agent per call with `messages=[]` and a `NullConversationManager`.
- **Production fallback to fixture data.** Already refused, twice: in
  `GatewaySettings.from_env` and again in `build_gateway`.
- **Non-configurable model identifiers.** Already impossible. No default is compiled
  in and the identifier is always passed explicitly.
- **Exact token counting.** Already present, with no fallback to the heuristic outside
  fixture mode (ADR-011).
- **Dreamer lineage.** The Dreamer is `arm_summary`, and its lineage is the domain's
  existing summary lineage: `MemoryKind.SUMMARY`, at least two `parent_memory_ids`,
  and a `MemoryLineageEdge` per compressed source. Nothing was missing; the pilot
  supplies the parameters and adds no second mechanism.
- **Mandatory citation-auditor calls.** Not present in Phases 1-3, because nothing
  sequenced a cycle. It became a Phase 4 design constraint instead, and the engine
  makes no auditor call at all.

Two pieces of existing capability the pilot does not use are configured off rather
than removed: the two reference arms (`arm_full`, `arm_stateless`) are simply not in
`protocol.arms`, and the citation auditor stays fully implemented in the gateway
behind `CitationMode.AUDITED`, which the engine refuses rather than silently
downgrades.

### Delivered

- `experiment/pilot/` - five machine-readable protocol files, a predictions document,
  and a generated `manifest.json`. The Station Kestrel seed world (twelve memories), a
  twenty-four stimulus deck across five phases, a twelve-fact truth ledger, a
  ten-question checkpoint interview, and the protocol that binds them. Every file
  carries a schema version, a protocol version, a status, a title, a description, a
  creation time, and a digest of its own content; the manifest collects every digest
  and the prompt-template hashes in one place.
- `packages/pilot` - the application service. Protocol loading, cross-file validation,
  calibration, and freezing; a typed `PilotRunConfiguration`; a per-cycle and per-run
  model-call budget; immutable `RunSnapshot` and `ArmCycleSnapshot` records with
  canonical-JSON digests; the cycle engine; the local export; and the commands.
- Protocol lifecycle `DRAFT` -> `LOCAL_VALIDATED` -> `AWS_CALIBRATED` -> `FROZEN` ->
  `RETIRED`, enforced by the commands rather than by convention. A draft runs nothing,
  `local-validate` refuses an uncalibrated budget, and a validated file edited
  afterwards is detected by recomputing its digest. **The pilot stops at
  `LOCAL_VALIDATED`**: `promote_documents` refuses to write `AWS_CALIBRATED` or
  `FROZEN`, because the budget is denominated in a local approximate counter and
  freezing that would make the canonical experiment a measurement of the fixture
  tokeniser. Editing means `make pilot-draft`, which clears every digest. The digest
  covers parsed content, so reflowing YAML is not a modification and changing a
  stimulus is.
- `run_kind` (`LOCAL_FIXTURE` / `AWS_STAGING` / `AWS_CANONICAL`) on the run
  configuration _and on every snapshot_, alongside `simulated` and
  `token_count_source`. A snapshot that travels out of its export still says what it
  is. `require_run_kind_consistent` refuses a canonical run on fixtures _and_ a local
  run on real models.
- Model-call usage recorded per run, cycle, arm, and operation, not only as a total: a
  cumulative count cannot answer which arm spent the Dreamer calls on cycle 14.
- The cycle engine: verify the cycle is next, load the one shared stimulus, generate
  six arms with bounded concurrency, validate every claimed citation against the
  arm's own state, fold the survivors into the statistics, admit the candidate, apply
  the mechanism, call the Dreamer only for a plan the mechanism emitted, stage all six
  results, check them across arms, and advance in a single assignment. An arm that
  fails leaves all six states exactly as they were.
- Model-call ceilings checked _before_ each call: six writers and at most two Dreamer
  summaries per cycle, no evaluator and no interviewer; six interviewers per
  checkpoint; a whole-run cap on top.
- `make pilot-validate`, `pilot-calibrate`, `pilot-local-validate`, `pilot-draft`,
  `pilot-local-cycle`, `pilot-local-run`, and `pilot-local-export`, over
  `scripts/validate_local_protocol.py`, `scripts/calibrate_local_budget.py`,
  `scripts/run_local_fixture_cycle.py`, and
  `scripts/run_local_fixture_experiment.py`.
- `docs/pilot/local-first-architecture.md`, `docs/adr/ADR-local-first-pilot.md`, and
  the generated `docs/pilot/local-token-calibration.md`
  (PROVISIONAL_LOCAL_APPROXIMATION).

### Verified results

The 24-cycle fixture run, executed:

- 144 cycle snapshots (6 arms x 24 cycles), 18 checkpoint records (6 arms x 3
  checkpoints), 171 model calls: 144 writer, 18 interviewer, 9 Dreamer summary. Zero
  evaluator and zero auditor calls.
- Seed set 157 budget tokens under `heuristic-v1`; budget calibrated to 240.
- Every arm ended within budget and every arm forgot something. Final active tokens:
  `arm_fifo` 234, `arm_lru` 238, `arm_heavy` 238, `arm_sink` 240, `arm_random` 231,
  `arm_summary` 221; retired counts 18, 17, 17, 17, 19, and 28 respectively. The six
  state hashes differ, which is the whole point of the run.
- The export writes eleven files plus `checksums.sha256`, which `sha256sum -c` verifies.

## Phase 5 - transactional local persistence, read API, analysis, export

Complete. The application is now persistent and fully local: SQLite, the local
filesystem, fixture models, and a local HTTP server. No AWS credential is required by
anything, and no AWS service is called.

### Delivered

- `packages/pilot/repositories.py` - twenty-six provider-independent ports, plus
  `RunRecord`, `CycleLock`, `PreparedCycle`, `StoredInterview`, `AnalysisStatus`, and
  `ExportManifestRecord`. The ports live with the application; adapters satisfy them.
- `packages/persistence` - the SQLite adapter and its migrations. Ten tables, each
  with a schema version and timestamps. `cycle_snapshots` and `interviews` carry
  ABORT triggers on UPDATE, so immutability survives code nobody has written yet.
- `packages/pilot/service.py` - lock, load, stage or reuse, prepare, commit, release.
  Every step idempotent on its own.
- The atomic commit: eleven checks and writes in one transaction, rolled back
  entirely on any failure. No partial cycle is ever visible.
- `packages/analysis` - Origin Recall, Identity Drift, Graveyard, Graveyard Echo,
  contradiction analysis, thirteen deterministic secondary metrics, and the
  eighteen-file dataset export.
- `packages/api` - the local read API. Sixteen routes, read-only by construction:
  no mutating verb is registered and a test asserts the route table contains only
  `GET`. Prepared cycles, future stimuli, evaluator notes, and prompt text are
  filtered out; prompt versions and hashes are published.
- `scripts/local_cli.py` (the composition root), `scripts/run_local_scheduler.py`,
  and `scripts/verify_run.py`.
- `make local-db-migrate`, `local-run-create`, `local-cycle`, `local-status`,
  `local-scheduler`, `local-api`, `local-analyze`, `local-export`, `local-verify`,
  `local-reset-demo`, and `local-all`.
- `docs/pilot/local-backend.md`.

### Verified results

The 24-cycle SQLite run, executed end to end by `make local-all`:

- 144 committed snapshots, 18 interviews at cycles 0, 12, and 24, and 171 model
  calls: 144 writer, 18 interviewer, 9 Dreamer summary. Zero evaluator, zero auditor.
- The same final states as the Phase 4 in-memory run - `arm_fifo` 234, `arm_lru` 238,
  `arm_heavy` 238, `arm_sink` 240, `arm_random` 231, `arm_summary` 221 - which is the
  cross-check that persistence changed the storage and not the experiment.
- 252 metric rows, 116 Graveyard entries, 102 echo measurements, 180 contradiction
  classifications, and divergence matrices at all three checkpoints.
- The export writes sixteen files plus `checksums.sha256`; all sixteen verify.
- `scripts/verify_run.py` passes all sixteen checks.

### Important decisions

- **The composition root left the pilot package.** The import-boundary test caught
  `pilot/local.py` importing the SQLite adapter and the analysis package. An
  application that imports its own adapter has no adapter line left to move in
  Phase 7, so the commands moved to `scripts/local_cli.py`.
- **Checkpoint spend is recorded separately.** A checkpoint follows the commit that
  snapshotted usage, so `add_usage` folds the interviewer calls in afterwards.
  Without it the run's totals silently excluded eighteen calls.
- **`check_same_thread=False` on the connection**, because the read API serves sync
  endpoints from a threadpool. Safe only because every write goes through one
  `BEGIN IMMEDIATE` transaction and the API never writes.

## Phase 6 - the local exhibition and the release candidate

Complete. Attention Sink now runs as a whole product locally: SQLite behind the
application, fixture models, a local read API, and a React exhibition reading that
API. Only AWS adapters and real-model execution remain.

### Delivered

- Validated frontend configuration (`VITE_API_BASE_URL`, `VITE_PUBLIC_RUN_ID`,
  `VITE_DEPLOYMENT_MODE`, `VITE_POLL_INTERVAL_MS`, `VITE_FIXTURE_MODE`). A
  production-like build with no API configuration refuses to start; every default
  fails towards "this is simulated".
- Seven routes: Six Minds (`/` and `/cycle/:cycle`), Graveyard, Graveyard Echo,
  memory detail with lineage, Timeline, Interviews, Methodology.
- The public names - Goldfish, Present-Minded, Pragmatist, Keeper of the First Day,
  Gambler, Dreamer - exist in `apps/web/src/arms.ts` and nowhere else. Not in the
  protocol, the database, any API response, or any prompt (ADR-004).
- An accessible Timeline: SVG with a caption, a table carrying the same figures, a
  keyboard-operable scrubber, and no claim that geometric distance shows causation.
- Polling that stops when the tab is hidden, never overwrites a selected historical
  cycle, caches immutable records, and says which command to run when the API is
  unreachable. No WebSockets.
- `packages/api` gained CORS for the local exhibition origins, and three routes the
  frontend needs: `/echoes`, `/contradictions`, `/question-scores`, backed by a new
  `analysis_artifacts` table (migration 2).
- `make pilot-local-demo`, `pilot-local-build`, `pilot-local-e2e`, and
  `pilot-local-release-check`.
- 62 Playwright checks across desktop and mobile projects, covering the fourteen
  named flows plus 24 accessibility assertions.
- `docs/pilot/local-release-readiness.md` and
  `docs/pilot/local-requirements-traceability.md`.

### Two defects the suites found

- **One SQLite connection shared across a threadpool.** Phase 5 opened the database
  with `check_same_thread=False` and argued it was safe. It was not: the flag
  silences the thread check but a connection is still not re-entrant, and the read
  API serves synchronous endpoints from Starlette's threadpool. Under Playwright it
  raised `sqlite3.InterfaceError`. Fixed with one connection per thread.
- **Pages had no heading while loading or failing.** Every route returned its `h1`
  only after data arrived, so a slow or failed load left no heading at all - worst
  exactly when a reader most needs to know where they are.

### Verified results

`make pilot-local-release-check` from an empty checkout: 24 cycles, 144 snapshots, 18
interviews, 171 model calls, 252 metrics, 116 Graveyard entries, 102 echo
measurements, 180 contradiction classifications, a 17-file export whose checksums all
verify, 16/16 verification checks, a 239.76 kB production build, and 62 passing
browser flows.

### Test and coverage position

- 790 tests: unit, Hypothesis property, and integration. 787 run; the three Bedrock
  contract tests are skipped unless `AS_BEDROCK_CONTRACT_TESTS=1`. 184 are new in this
  phase: 181 across ten `test_pilot_*` modules, and three added to the import-boundary
  suite for the new application package.
- **99%** of `packages/pilot`, gated at 95% in `make test` alongside the other three.
  All four packages pass their own gate.

### Deliberately not delivered in this phase

- No persistence. The engine holds the run in memory; the export writes files and
  nothing reads them back. There is no DynamoDB, no S3, and no projection.
- No public API, no WebSocket, no CDK resource, and no deployment.
- No metrics computation. The truth ledger and the interview protocol define what
  would be scored; nothing scores it yet. The autobiography at cycle 24 is produced
  and stored, not graded.
- No evaluator or citation-auditor calls on the cycle path. Both adapters exist and
  are tested; the pilot protocol declares an allowance of zero for each.
- No forks and no moderation.
- The Lambda orchestrator named in ADR-008-pilot as the deployment target is not
  built. The engine is persistence-independent so that it can be, but this phase runs
  it locally only.

## Pilot Phase 7 - AWS adapters, CDK infrastructure, staging deployment, real Bedrock

**Status: complete, with one blocker on canonical execution recorded rather than
worked around.**

The same domain logic, application services, API contracts, and frontend now run on
AWS behind different adapters. Nothing above the adapter line changed to fit AWS: the
services still hold the ports Phase 5 defined, and `PilotService` and
`AnalysisService` are byte-for-byte the ones the local process runs.

### Delivered

- `packages/aws` - the one package above the model gateway that imports an AWS SDK.
  `DynamoRepository` (the second implementation of `PilotRepository`),
  `S3ExportStorage`, the structured logger, the cycle-completed event, the deployment
  settings, the composition root, and the three Lambda handlers.
- **One table, one partition per run.** `RUN#{run_id}` holds the run's head, six arm
  states, every prepared cycle, every snapshot, every interview, every metric, the
  analysis markers, and the export manifests. One sparse index (`GSI1`) serves the two
  access patterns the main key cannot: the newest-first run listing and one arm's
  snapshots in cycle order. **No read path issues a `Scan`**, and no role has
  permission to.
- **The commit is one `TransactWriteItems`.** Fourteen writes -- six snapshots, six arm
  states, the prepared cycle, the run head -- conditioned on the run's version, its
  current cycle, the lock token, and the prepared cycle's content hash. The lock lives
  on the run's own item, so "the run is where I left it" and "the lock is still mine"
  are one condition on one item.
- **Immutability is a write condition**, because DynamoDB has no triggers: a snapshot
  is written with `attribute_not_exists`, so a rewrite fails rather than replacing a
  committed record.
- Three Lambda handlers, thin over the existing services. The run-cycle handler
  executes one cycle per invocation and returns a status for every refusal that is not
  a fault; the analysis handler verifies the store against the event before it
  believes it, claims the cycle with a conditional write, and releases the claim if the
  work fails; the read handler is `build_app` behind Mangum -- the same routes, schemas,
  and filters the local process serves.
- `PilotStack` in three environment configurations. Every dangerous default is off in
  all three: execution disabled, schedule disabled, nothing canonical. Staging caps
  the run at three cycles so that arming it by mistake costs three cycles rather than
  twenty-four.
- Least-privilege IAM written out rather than granted. `grantReadWriteData` would add
  `Scan`, `BatchWriteItem`, and `DeleteItem` to every role that writes anything; naming
  the actions is longer and is the point. The read API's role holds `GetItem` and
  `Query` and no write action of any kind. **No wildcard action anywhere**, and Bedrock
  is scoped to the configured model identifiers.
- CloudFront with Origin Access Control over a private bucket, SPA fallback,
  compression, and a content security policy that names the API's own domain rather
  than allowing any origin. Both buckets block all public access; the export bucket is
  versioned and its canonical prefix refuses a second write.
- Structured CloudWatch logging with a **closed** field set -- the allowlist is the
  whole mechanism, so no prompt, journal entry, memory, stimulus, interview answer, or
  token can reach a log line. Four metric filters derive the experiment's own metrics
  from those lines, and nine alarms cover the seven conditions the brief names.
- `scripts/aws_cli.py`, the AWS composition root: `preflight`, `bootstrap`, `status`,
  `cycle`, `schedule inspect|enable|disable|invoke-once`, and `export`. Enabling the
  schedule is refused while the function itself is disarmed.
- `scripts/build_lambda_bundle.py`, which builds the deployment package as its own
  inspectable step so that `cdk synth` needs neither Docker nor a network.
- ADR-012, and `docs/pilot/aws-staging-report.md` and
  `docs/pilot/aws-teardown.md`.

### Verified results

Deployed to a staging account in `us-east-1`, on `amazon.nova-micro-v1:0` and
`amazon.titan-embed-text-v2:0`.

- **Three real six-arm cycles committed**, each through the deployed Lambda, in 5.6 to
  7.0 seconds. 18 snapshots, 6 cycle-0 interviews, 24 model calls (18 writer, 6
  interviewer; zero auditor, zero evaluator on the cycle path), 57,279 input and
  14,608 output tokens.
- **Analysis ran asynchronously** for all three cycles, triggered by the
  `CycleCompleted` event, and stored 180 metric rows and the four derived artefacts.
- **Duplicate execution is idempotent.** An invocation naming a committed cycle
  returned `already_committed` and spent nothing; a redelivered event returned
  `already_analysed`.
- **The public API exposes only committed data.** `/cycles` returned `[1, 2, 3]`, a
  request for cycle 4 returned 404 _while a prepared cycle existed for it_, and every
  mutating verb returned 404 on every path.
- **Both buckets are private**; direct S3 access returns 403 and the exhibition is
  served only through CloudFront with Origin Access Control, a content security policy
  naming one API domain, HSTS, and SPA fallback.
- **The dead-letter path works**: a malformed event published to the bus was refused,
  retried twice by EventBridge, and dead-lettered. The `AnalysisErrorsAlarm` fired for
  a real failure.
- **No log line carries content.** All three log groups were grepped for eleven probes
  -- seed text, prompt field names, the prompt boundary token, `AKIA`,
  `Authorization`, an arm identifier -- and none appeared.
- The 16-file export verified: all 16 digests recomputed from S3 matched.
- **The scheduler exists and has never been enabled.** The run-cycle function is
  disarmed again.

Full detail, including the failure tests and what each proved, is
`docs/pilot/aws-staging-report.md`.

### Test and coverage position

- 1,138 tests: unit, Hypothesis property, integration, and smoke. 1,127 run; the 8
  Bedrock smoke tests and 3 contract tests are skipped unless explicitly armed.
- 12 new test modules, including `tests/integration/test_dynamodb.py`, which restates
  the Phase 5 SQLite guarantees against the DynamoDB adapter rather than assuming they
  carried over, and 38 CDK assertions that run before any deployment.
- **100%** of `packages/aws`. Every package passes its own 95% gate: domain 99%,
  policies 100%, model_gateway 99%, pilot 98%, analysis 97%, persistence 99%,
  api 99%, aws 100%.

### Three defects this phase found

Two could only have been found by deploying, and the third by measuring coverage.

- **A Bedrock model identifier does not fit in a `Version`.** `amazon.nova-micro-v1:0`
  carries a colon; fixture mode returns `fixture-evaluator-v1`, which is already
  version-shaped, so every local run passed and the first deployed analysis failed on
  its first metric. Fixed with `attention_sink.domain.version_token`.
- **The export and the API labelled real generations as simulated fixtures.**
  `EXPORT_LABELS` and `ApiEnvelope.simulated` were constants. Both are now derived
  from the run, as four independent labels.
- **A session-wide skip in the smoke conftest.** `pytest_collection_modifyitems` is a
  session hook, so a conftest in a subdirectory is handed every collected item -- and
  the smoke guard was skipping the entire suite for anyone running bare `pytest`.
  `make test` now collects every directory under `tests/`, so the coverage gates catch
  it.

### The blocker, stated plainly

**Bedrock `CountTokens` is unavailable for every model this account can reach.** Every
on-demand text model in `us-east-1` returns `ValidationException: The provided model
doesn't support counting tokens`, including the Nova family the staging run uses; so
does every Anthropic inference profile the account can reach, and so do us-west-2,
us-east-2, eu-central-1, and ap-northeast-1.

ADR-011 makes the model's own tokenisation the production unit with no fallback, and
the engine counts on every cycle, so with no counter there is no cycle at all.
ADR-012 resolves it the only honest way: `TOKEN_COUNT_SOURCE` **declares** the counter
before the run starts, the choice is recorded in the manifest and in every export, and
`require_run_kind_consistent` refuses a canonical run denominated in an approximate
one. There is still no fallback -- `BedrockTokenCounter` raises when `CountTokens` is
unavailable and nothing catches it.

The consequence is that the canonical twenty-four-cycle run is blocked until
`CountTokens` covers a usable model, and that the protocol stays `LOCAL_VALIDATED`
rather than `AWS_CALIBRATED`: calibrating a budget against a counter the canonical run
will not use would produce a number nobody should freeze.

### Deliberately not delivered in this phase

- **No canonical run.** None was created, and the machinery refuses to create one:
  `AS_CANONICAL` is rejected outside production, and a canonical run needs a `FROZEN`
  protocol, which nothing in this phase produces.
- No Step Functions and no event ledger. Both remain accepted decisions
  (ADR-002, ADR-003) deferred by ADR-local-first-pilot.
- No WebSocket API. The exhibition polls.
- No SNS topic behind the alarms. They fire and are visible; wiring a notification
  channel needs an address this repository should not hold.
- No production deployment. `preflight` refuses `AS_DEPLOYMENT_ENVIRONMENT=production`.

## Phase 8

Complete. The canonical run exists, is finished, and is public.

`CountTokens` turned out to be unavailable on every model this account can reach, so
the exact counter was reached a different way: one `Converse` call with
`maxTokens=1` returns `usage.inputTokens`, which is the writer model's own
tokenisation of the request. ADR-013 records the decision; ADR-012 is superseded in
part. The requirement was always the number, never the operation, and there is still
no fallback to an approximate count.

With an exact counter the budget was calibrated against `amazon.nova-micro-v1:0` and
frozen at 208 tokens. The protocol advanced `LOCAL_VALIDATED → AWS_CALIBRATED →
FROZEN`, and `experiment/pilot/canonical-run-manifest.json` pins the models,
inference settings, prompt hashes, policy parameters, metric versions and commit that
a launch is checked against — a different model ID, budget, prompt hash or edited
protocol file each cause rejection, and each has a test.

`run_aws_canonical` then ran twenty-four cycles on EventBridge Scheduler: 144
snapshots, 157 Graveyard entries, 18 interviews at cycles 0, 12 and 24, 2,062 metric
rows, 1,429 model calls, and six arms that ended with 9, 12, 12, 12, 11 and 13 active
memories. Twenty-six invariant checks pass against it. The dataset is exported to the
immutable canonical prefix as eighteen files whose checksums verify with `sha256sum -c`
and no tool from this repository.

The exhibition is at https://d1qskxceo899me.cloudfront.net, reading a public API with
no mutation route, over private buckets, behind Origin Access Control and a
restrictive content security policy. The scheduler is disabled and the run-cycle
function is disarmed.

### Deliberately not delivered in this phase

- No Step Functions and no event ledger. Both remain accepted decisions
  (ADR-002, ADR-003) deferred by ADR-local-first-pilot.
- No WebSocket API. The exhibition polls.
- No SNS topic behind the alarms. They fire and are visible; wiring a notification
  channel needs an address this repository should not hold.
- No second run, no second model, and no repetition. One observation is not an
  effect size, and the Methodology page says so.
