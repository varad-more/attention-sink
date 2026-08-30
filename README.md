# Attention Sink

Six generative agents begin with identical seed memories, receive the same ordered
stimuli, use the same writer model and inference settings, and operate under the
same fixed active-memory token budget.

They differ in exactly one thing: the mechanism that decides what to forget when the
budget is exceeded.

| Internal arm  | Mechanism                                             |
| ------------- | ----------------------------------------------------- |
| `arm_fifo`    | Evict oldest first                                    |
| `arm_lru`     | Evict least recently cited, by verified citations     |
| `arm_heavy`   | Retain the most-cited memories                        |
| `arm_sink`    | Pin the origin memories, slide a window over the rest |
| `arm_random`  | Evict uniformly at random from a recorded seed        |
| `arm_summary` | Compress old memories into lossy summaries            |

Two optional reference arms bound the result: one that forgets nothing and one that
remembers nothing.

## The run

One canonical run exists. `run_aws_canonical` ran twenty-four cycles on real Amazon
Bedrock models under a frozen protocol, and the exhibition that shows it is public:

**https://d1qskxceo899me.cloudfront.net**

Twenty-four cycles, six arms, 144 immutable snapshots, 157 retired memories, 18
interviews at cycles 0, 12 and 24, and an eighteen-file dataset whose checksums verify
with `sha256sum -c` and nothing from this repository. The six mechanisms ended holding
9, 12, 12, 12, 11 and 13 active memories from the same 208-token budget. What that
means, and what a single run cannot mean, is on the Methodology page.

## What this is, and what it is not

This is an experiment in **application-level episodic memory**: explicit memory
records that the application supplies to a model as context. It makes no claim about
the model's internal KV cache, attention matrices, hidden state, or experience. The
mechanism under study is the one the application controls. See
[ADR-001](docs/adr/001-application-level-memory.md).

## Getting started

Prerequisites: Python 3.12, Node 20.19+ or 22.12+, and [uv](https://docs.astral.sh/uv/).

```bash
make bootstrap   # install Python and Node dependencies, and the git hooks
make verify      # lint, typecheck, test, and CDK synth - everything CI runs
```

No AWS account or credentials are needed for either command, or for `make dev`.

To watch the six mechanisms diverge on identical input, without a model or a network:

```bash
make simulate FIXTURE=datasets/fixtures/policy_simulator/divergence.json
```

```bash
make dev         # web client at http://localhost:5173, against fixture data
```

The client runs in **local mode** by default and says so on screen: every figure it
shows comes from a fixture, not from a run. Production mode requires a Region and
all five model identifiers, and refuses to start without them, so simulated output
can never be mistaken for a result.

Model access is a separate switch. `MODEL_MODE=fixture` is the default and needs no
AWS account: it serves deterministic responses through the same typed gateway,
prompts, and validation that production uses, and marks everything it produces as
simulated. `MODEL_MODE=bedrock` makes real calls and fails closed without a Region
and every model identifier. A production runtime may not run on fixtures at all.

```bash
make test-contract   # opt-in checks against real Bedrock; costs money, skipped by default
```

## Running the pilot

The whole 24-cycle experiment runs locally, on fixtures, in about a second:

```bash
make pilot-validate      # do the protocol files agree, and has any validated one been edited?
make pilot-local-run     # six arms, 24 cycles, exported to .pilot-runs/local
```

The export carries a `checksums.sha256` that `sha256sum -c` verifies, and everything in
it is marked simulated, local, and non-canonical. Editing the protocol means
`make pilot-draft`, then `make pilot-calibrate` and `make pilot-local-validate` before a
run will start again: a run refuses draft files, and a validated file edited afterwards
is detected rather than run.

The pilot protocol stops at `LOCAL_VALIDATED`. It is frozen only after AWS token
calibration, because its budget is currently denominated in a local approximate
counter. See [docs/pilot/local-first-architecture.md](docs/pilot/local-first-architecture.md).

The whole application also runs persistently, on SQLite and a local HTTP server, with
no AWS credential:

```bash
make local-all           # empty database to verified dataset export
make pilot-local-demo    # the whole product: database, run, API, and exhibition
```

The exhibition is a React client reading that API — Six Minds, the Graveyard, a
Timeline, the checkpoint Interviews, and a Methodology page that states what the
experiment cannot tell you. Every page is labelled `LOCAL SIMULATION`.

See [docs/pilot-scope.md](docs/pilot-scope.md) for what the pilot narrows and why, and
[docs/pilot/local-backend.md](docs/pilot/local-backend.md) for the persisted backend.

## Deploying to AWS

The same domain logic, application services, API contracts, and frontend run on AWS
behind different adapters: DynamoDB instead of SQLite, S3 instead of a directory,
three Lambdas instead of a command line, Bedrock instead of fixtures.

**Everything deploys inert.** Execution is disabled, the schedule is disabled, and no
environment is canonical -- in all three configurations. Arming a deployment is an
explicit operator action against a deployed stack, never a consequence of `cdk deploy`.

```bash
make aws-preflight       # account, Region, model access, and that nothing is armed
make aws-deploy          # bundle, deploy, build the exhibition, deploy again
make aws-bootstrap       # create the staging run: six identical seeds, cycle 0
make aws-cycle           # one cycle, once, against real models
make aws-schedule-inspect
make aws-destroy         # everything, in one command
```

`make aws-smoke` runs the Bedrock smoke tests; they cost money and are skipped unless
`ALLOW_BEDROCK_CALLS=1`. What a deployment created and how to remove it is
[docs/pilot/aws-teardown.md](docs/pilot/aws-teardown.md); what the
staging deployment actually proved is
[docs/pilot/aws-staging-report.md](docs/pilot/aws-staging-report.md).

## Repository layout

```
apps/web/            React client
packages/            Importable libraries; only packages/aws imports an AWS SDK
packages/aws/        The DynamoDB and S3 adapters and the three Lambda handlers
infrastructure/cdk/  AWS CDK v2 application
experiment/pilot/    The Station Kestrel protocol: seeds, stimuli, ledger, manifest
scripts/             Local runners and the two composition roots (local_cli, aws_cli)
datasets/fixtures/   Deterministic fixtures for local mode
docs/                Architecture, methodology, operations, and decision records
tests/               unit, property, integration, e2e
```

## Documentation

- [Implementation status](docs/implementation-status.md) - what is built, and what is not
- [Memory policies](docs/memory-policies.md) - the exact algorithm and tie-breaker for every arm
- [Model gateway](docs/model-gateway.md) - the seven model roles, prompts and their digests, schemas, failures, and metadata
- [Pilot scope](docs/pilot-scope.md) - what the 24-cycle pilot runs, and where it narrows the production design
- [Release readiness](docs/pilot/release-readiness-report.md) - the decision, the sixteen conditions, and the limitations
- [Requirements traceability](docs/pilot/final-requirements-traceability.md) - every requirement and the evidence it holds
- [AWS token calibration](docs/pilot/aws-token-calibration.md) - how the 208-token budget was derived, and against what
- [AWS cost and usage](docs/pilot/aws-cost-and-usage-report.md) - what the canonical run actually spent
- [AWS staging report](docs/pilot/aws-staging-report.md) - what the staging deployment proved before the canonical run
- [AWS teardown](docs/pilot/aws-teardown.md) - every resource a deployment creates, and how to remove it
- [System context](docs/architecture/system-context.md)
- [Container view](docs/architecture/container-view.md)
- [Architecture decision records](docs/adr/)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Licence

Apache-2.0.
