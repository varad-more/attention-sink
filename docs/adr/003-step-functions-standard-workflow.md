# ADR-003: Step Functions Standard Workflow with a six-arm inline map

Status: accepted, 2026-08-29.

## Context

A cycle is a long, branching, failure-prone sequence: prepare the cycle, then for
each of six arms generate a thought, audit its citations, plan a rebalance, possibly
generate a summary, and commit; then finalise. Model calls are slow and fail in
interesting ways. Every step must be individually observable, because "the cycle
failed" is not a usable statement about a scientific run.

The obvious alternatives are a single long-running Lambda, which cannot survive its
own timeout and hides its internal state, and a chain of queues, which would leave us
building orchestration semantics by hand.

## Decision

Each cycle is one execution of a **Step Functions Standard Workflow**. The six arms
run under an **inline `Map` state** with the concurrency the protocol specifies.

Standard, not Express: we need per-step execution history retained and queryable,
executions that can run for hours, and exactly-once state transitions. Express
workflows offer none of those and are priced for a throughput we do not have.

Inline `Map`, not distributed: the fan-out is exactly six, known in advance and
bounded by the experimental design. A distributed map would add S3 round-trips and
child-execution overhead for a fan-out that fits comfortably inline.

State machine payloads carry identifiers only. Memory histories are read from the
store by the step that needs them, never passed between states, because the payload
size limit is not a budget any real memory history should be measured against.

A failed arm sends its message to an SQS dead-letter queue rather than failing the
cycle silently, and the cycle is not marked committed until every arm has committed.

## Consequences

- Every step is separately retryable, separately timed, and separately visible in
  execution history, which is also the operational audit trail.
- Uniformity across arms is structural: the same state definition runs for all six,
  so an arm cannot accidentally get a different pipeline.
- The concurrency limit is an experimental parameter and is versioned with the rest
  of the protocol.
- Local development cannot run the state machine. Services are therefore written as
  plain functions with the handler as a thin adapter, so the pipeline can be
  exercised in tests without Step Functions.

## Revisit when

The arm count stops being a small fixed number - a large ablation sweep would justify
a distributed map - or a cycle's step count grows past what one execution history
usefully displays.
