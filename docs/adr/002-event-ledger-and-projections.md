# ADR-002: Immutable event ledger plus mutable projections

Status: accepted, 2026-08-29.

## Context

The central claim of this project is causal: _this_ memory was evicted for _this_
reason, and that is why the agent later said _that_. A store that only holds current
state cannot support such a claim. By the time a divergence is interesting, the
state that produced it has been overwritten.

We also need to replay a committed cycle exactly, both to verify results and to let
a reader inspect any moment in an agent's history.

## Decision

Every fact is recorded twice, with different lifetimes.

- **Events** are immutable and append-only: a cycle started, a thought was
  generated, citations were audited, a rebalance was planned, a summary was written,
  an arm-cycle was committed. An event is never updated or deleted.
- **Projections** are mutable current-state views derived from events: an arm's
  active memory, a run's status, published metrics. A projection can be rebuilt from
  the ledger at any time and is therefore disposable.

The two are stored separately. A committed cycle is replayable from a stored
snapshot plus the events since it. Writes use optimistic concurrency and idempotency
keys, and a cycle commit is a single transactional write so that an arm can never be
observed half-committed.

## Consequences

- Provenance is a query, not an archaeology exercise. "Why is this memory gone?" is
  answered by the plan event that evicted it, which carries the ranking key that
  selected it.
- Projections can be rebuilt after a bug in projection code without touching the
  historical record, which is the only kind of repair that does not compromise a run.
- Storage grows monotonically. Accepted: the ledger is the asset.
- Every writer must be idempotent, because at-least-once delivery is the only
  delivery guarantee available. This is a real cost paid by every service.
- Reading current state means reading a projection, never folding the ledger on the
  request path.

## Revisit when

Ledger volume makes replay impractical at the scale a run actually reaches, at which
point periodic snapshots become mandatory rather than an optimisation.
