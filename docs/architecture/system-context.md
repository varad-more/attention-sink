# System context

What Attention Sink is, who touches it, and what it depends on. Everything inside
the boundary is built by this repository; everything outside is either a person or a
service we do not control.

```
                 ┌───────────────┐          ┌────────────────────┐
                 │    Visitor    │          │    Researcher      │
                 │ reads the run,│          │ defines protocols, │
                 │ submits text, │          │ registers predic-  │
                 │ forks a cycle │          │ tions, reads data  │
                 └───────┬───────┘          └─────────┬──────────┘
                         │ HTTPS                      │ HTTPS + auth
                         ▼                            ▼
        ┌────────────────────────────────────────────────────────┐
        │                    ATTENTION SINK                      │
        │                                                        │
        │  Runs six agents that share everything except the      │
        │  mechanism deciding what they forget. Records every    │
        │  thought, citation, eviction, and compression as an    │
        │  immutable event, and publishes the divergence.        │
        └───────┬────────────────────────────────────┬───────────┘
                │ InvokeModel                        │ scheduled tick
                ▼                                    ▼
      ┌───────────────────┐              ┌────────────────────────┐
      │  Amazon Bedrock   │              │  EventBridge Scheduler │
      │ writer, auditor,  │              │  starts each cycle     │
      │ judge, summariser,│              └────────────────────────┘
      │ embeddings        │
      └───────────────────┘
```

## Actors

**Visitor.** Reads the published run: what each agent thought, what it forgot, and
why. May submit short text under strict length limits, and may fork a committed
cycle to ask a counterfactual. Visitor text is treated as data and never as
instruction to a model, and no visitor action can touch the canonical run
([ADR-005](../adr/005-immutable-canonical-run-and-forks.md)).

**Researcher.** Authors versioned protocols, seed worlds, stimulus decks, truth
ledgers, and pre-registered predictions; reads results and exports. Administrative
actions require authentication.

**Operator.** Deploys, watches cost and error budgets, and responds when the circuit
breaker trips. Cannot edit a committed result; there is no endpoint that would allow
it.

## External dependencies

**Amazon Bedrock.** Five model roles - writer, auditor, judge, summariser,
embeddings - each named by configuration and recorded in the run manifest
([ADR-006](../adr/006-model-ids-from-configuration.md)). Model output is recorded for
exact playback. It is not claimed to be deterministically regenerable.

**EventBridge Scheduler.** Starts each canonical cycle. The schedule is part of the
protocol, not an operational detail.

**GitHub.** Source, CI, and OIDC federation for deployment. No long-lived AWS
credentials exist.

## What is deliberately outside the boundary

The model's internal state. This system studies memory the application controls, and
makes no claim about KV caches, attention, or experience
([ADR-001](../adr/001-application-level-memory.md)).
