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

```bash
make dev         # web client at http://localhost:5173, against fixture data
```

The client runs in **local mode** by default and says so on screen: every figure it
shows comes from a fixture, not from a run. Production mode requires a Region and
all five model identifiers, and refuses to start without them, so simulated output
can never be mistaken for a result.

## Repository layout

```
apps/web/            React client
services/            One directory per Lambda handler
packages/            Importable libraries, no AWS dependency below the adapter line
infrastructure/cdk/  AWS CDK v2 application
experiments/         Versioned protocols, seed worlds, stimulus decks, predictions
datasets/fixtures/   Deterministic fixtures for local mode
docs/                Architecture, methodology, operations, and decision records
tests/               unit, property, integration, e2e
```

## Documentation

- [Implementation status](docs/implementation-status.md) - what is built, and what is not
- [System context](docs/architecture/system-context.md)
- [Container view](docs/architecture/container-view.md)
- [Architecture decision records](docs/adr/)
- [Contributing](CONTRIBUTING.md)
- [Security policy](SECURITY.md)

## Licence

Apache-2.0.
