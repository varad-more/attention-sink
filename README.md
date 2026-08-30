# Attention Sink

Six generative agents start with the same twelve seed memories, read the same
twenty-four stimuli in the same order, run on the same model with the same inference
settings, and live under the same fixed active-memory budget.

One thing differs between them: what they throw away when the budget runs out.

| Internal arm  | Mechanism                                             |
| ------------- | ----------------------------------------------------- |
| `arm_fifo`    | Evict oldest first                                    |
| `arm_lru`     | Evict least recently cited, by verified citations     |
| `arm_heavy`   | Retain the most-cited memories                        |
| `arm_sink`    | Pin the origin memories, slide a window over the rest |
| `arm_random`  | Evict uniformly at random from a recorded seed        |
| `arm_summary` | Compress old memories into lossy summaries            |

Two optional reference arms bound the result: one forgets nothing, one remembers
nothing.

## The run

`run_aws_canonical` ran twenty-four cycles on Amazon Bedrock under a frozen protocol.
It is the only canonical run, and the exhibition that shows it is public:

**https://d1qskxceo899me.cloudfront.net**

Six arms, 144 immutable snapshots, 157 retired memories, and interviews at cycles 0,
12 and 24. From the same 208-token budget, the six mechanisms finished holding 9, 12,
12, 12, 11 and 13 active memories.

That is one run, one model, one seed world, no repetition. It shows the mechanisms
diverge. It does not measure by how much, and the Methodology page says so before it
says anything else.

The dataset is eighteen files. Its `checksums.sha256` verifies with `sha256sum -c` and
nothing else from this repository.

## What this is not

This studies **application-level episodic memory** — explicit records the application
hands a model as context. It says nothing about the model's KV cache, attention
matrices, hidden state, or experience. The mechanism under study is the one the
application controls, which is the only one it can change. See
[ADR-001](docs/adr/001-application-level-memory.md).

## Getting started

You need Python 3.12, Node 20.19+ or 22.12+, and [uv](https://docs.astral.sh/uv/).
No AWS account, for any of this.

```bash
make bootstrap   # Python and Node dependencies, plus the git hooks
make verify      # lint, typecheck, test, CDK synth - exactly what CI runs
make dev         # web client on http://localhost:5173, against fixtures
```

To watch the six mechanisms come apart on identical input, with no model and no
network:

```bash
make simulate FIXTURE=datasets/fixtures/policy_simulator/divergence.json
```

Two switches keep simulation from being mistaken for a result. The client runs in
local mode by default and prints `LOCAL SIMULATION` on every page. Separately,
`MODEL_MODE=fixture` serves deterministic responses through the same gateway, prompts
and validation that production uses, and marks its output simulated; `MODEL_MODE=bedrock`
makes real calls and refuses to start without a Region and all five model identifiers.
A production runtime cannot run on fixtures at all.

```bash
make test-contract   # real Bedrock, costs money, skipped by default
```

## Running the pilot

The whole twenty-four-cycle experiment runs locally, on fixtures, in about a second:

```bash
make pilot-validate    # do the protocol files agree, and has a validated one been edited?
make pilot-local-run   # six arms, 24 cycles, exported to .pilot-runs/local
```

Everything it writes is labelled simulated, local and non-canonical, and it ships its
own `checksums.sha256`.

Protocol `pilot-v1` is **frozen**. Its 208-token budget was calibrated against the
writer model's own tokeniser through Bedrock, so the number means something exact
rather than approximately. Frozen means the files refuse to change: edit one and the
next run rejects it by digest instead of quietly running against different input. A
new protocol is a new version — `make pilot-draft`, then calibrate, validate and
`make pilot-freeze` — never an edit to this one.

The full application also runs persistently on SQLite and a local HTTP server, still
with no AWS credential:

```bash
make local-all         # empty database through to a verified export
make pilot-local-demo  # the whole product: database, run, API, exhibition
```

## Deploying to AWS

The same domain logic, services, API contracts and frontend run on AWS behind
different adapters: DynamoDB for SQLite, S3 for a directory, three Lambdas for a
command line, Bedrock for fixtures.

**Everything deploys inert.** Execution off, schedule off, no environment canonical, in
all three configurations. Arming a deployment is a deliberate operator action against a
running stack — never a side effect of `cdk deploy`.

```bash
make aws-preflight   # account, Region, model access, and that nothing is armed
make aws-deploy      # bundle, deploy, build the exhibition, deploy again
make aws-bootstrap   # create the run: six identical seeds, cycle 0
make aws-cycle       # one cycle, once, against real models
make aws-verify      # 26 invariant checks against the stored run
make aws-destroy     # all of it, one command
```

Every resource a deployment creates, and how to remove it, is in
[the teardown guide](docs/pilot/aws-teardown.md).

## Layout

```
apps/web/            React client
packages/            Importable libraries; only packages/aws imports an AWS SDK
packages/aws/        DynamoDB and S3 adapters, and the three Lambda handlers
infrastructure/cdk/  AWS CDK v2 application
experiment/pilot/    Station Kestrel: seeds, stimuli, truth ledger, frozen manifest
scripts/             Local runners and the two composition roots
datasets/fixtures/   Deterministic fixtures for local mode
docs/                Architecture, methodology, operations, decision records
tests/               unit, property, integration, e2e
```

## Documentation

Start with [memory policies](docs/memory-policies.md) for the exact algorithm and
tie-breaker behind each arm, and the [model gateway](docs/model-gateway.md) for the
seven model roles and their prompt digests.

For the run itself: [release readiness](docs/pilot/release-readiness-report.md) has the
decision and the limitations, [token calibration](docs/pilot/aws-token-calibration.md)
explains where 208 came from, and [cost and usage](docs/pilot/aws-cost-and-usage-report.md)
records what it spent.

The rest — architecture views, decision records, [contributing](CONTRIBUTING.md),
[security](SECURITY.md) — is under [`docs/`](docs/).

## Licence

Apache-2.0.
