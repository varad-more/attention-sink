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

- `experiments/pilot/` - five machine-readable protocol files and one predictions
  document. The Station Kestrel seed world (twelve memories), a twenty-four stimulus
  deck across five phases, a twelve-fact truth ledger, a ten-question checkpoint
  interview, and the protocol that binds them. Every file carries a schema version, a
  protocol version, a status, a title, a description, a creation time, and a digest of
  its own content.
- `packages/pilot` - the application service. Protocol loading, cross-file validation,
  calibration, and freezing; a typed `PilotRunConfiguration`; a per-cycle and per-run
  model-call budget; immutable `RunSnapshot` and `ArmCycleSnapshot` records with
  canonical-JSON digests; the cycle engine; the local export; and the commands.
- Protocol lifecycle enforced by the commands rather than by convention. `DRAFT` may
  not run canonically, `freeze` refuses an uncalibrated budget, and a frozen file
  edited afterwards is detected by recomputing its digest. The digest covers parsed
  content, so reflowing YAML is not a modification and changing a stimulus is.
- The cycle engine: verify the cycle is next, load the one shared stimulus, generate
  six arms with bounded concurrency, validate every claimed citation against the
  arm's own state, fold the survivors into the statistics, admit the candidate, apply
  the mechanism, call the Dreamer only for a plan the mechanism emitted, stage all six
  results, check them across arms, and advance in a single assignment. An arm that
  fails leaves all six states exactly as they were.
- Model-call ceilings checked _before_ each call: six writers and at most two Dreamer
  summaries per cycle, no evaluator and no interviewer; six interviewers per
  checkpoint; a whole-run cap on top.
- `make pilot-validate`, `pilot-calibrate`, `pilot-freeze`, `pilot-local-cycle`,
  `pilot-local-run`, and `pilot-local-export`.

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
- The export writes ten files plus `checksums.sha256`, which `sha256sum -c` verifies.

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

## Phase 5 and later

Not started: persistence and the event ledger, Step Functions orchestration, metrics
computation, public API, WebSocket, forks, moderation, CDK resources, deployment, and
the experiment views in the web client.
