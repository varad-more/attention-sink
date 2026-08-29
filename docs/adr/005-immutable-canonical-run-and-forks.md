# ADR-005: The canonical run is immutable; exploration happens in forks

Status: accepted, 2026-08-29.

## Context

Two pressures act on a long-running public experiment. Visitors want to interact
with the agents and ask what-if questions. Researchers want the published result to
mean something. These are in direct conflict: every interaction that touches the
canonical timeline is an uncontrolled intervention in the middle of the experiment.

The tempting compromise - let visitors interact, and patch the data afterwards if it
goes wrong - destroys the result while leaving it looking intact.

## Decision

Once a cycle is committed it is immutable. No canonical experiment result is ever
edited by hand, by an administrative endpoint, or by a migration.

Everything else gets its own run identifier:

- **Community interactions** are recorded against a separate run. They never enter
  canonical active memory.
- **Causal forks** ("what if this memory had survived?") branch from a committed
  canonical snapshot into a new run identifier, and are labelled as forks
  everywhere they surface.
- **Interviews and evaluations are read-only probes.** Their output is never
  admitted to an agent's memory. An agent cannot learn about itself by being asked
  about itself.

The rule about eviction follows the same logic: an evicted memory never returns to
active context unless a separately recorded external event reintroduces equivalent
information, and that event is itself part of the ledger.

## Consequences

- A published result is reproducible from stored state, and stays reproducible after
  any amount of public interaction.
- Forks are a first-class product feature rather than a hazard: the counterfactual
  is the interesting part, and it costs the canonical run nothing.
- Every read path must carry the run identifier and label non-canonical data as
  such. A fork rendered as if it were canonical would be the worst bug this system
  could have.
- Fixing a genuine defect in a committed cycle is not possible. The correct response
  is a new run with a new protocol version, and an honest note about why.

## Revisit when

Never for the canonical run. The fork mechanism itself can evolve freely.
