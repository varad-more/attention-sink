# 10. Memories are shown to models under opaque per-request labels

Status: Accepted, 2026-08-29.

## Context

ADR-004 forbids any prompt from naming the mechanism under study. Phase 2 then gave
memories readable identifiers built from the arm and an arm-local sequence number:

```
mem_arm_fifo_000007
```

Those identifiers are the right thing for provenance. They make a citation audit, a
lineage edge, and a policy decision legible to a person reading stored output months
later, and they are unique within a run by construction.

They are also the wrong thing to put in a prompt. The writer must cite the memories
it used, so it must be given a handle for each one — and every handle of that form
tells the model exactly which arm it is, and therefore which memory policy governs
it. The leak is total and it is in every prompt of every cycle. A guard that scanned
prompts for arm names would fire on the one thing the prompt cannot do without.

We considered changing the identifier format so it carries no arm. That trades a
prompt problem for a provenance one: identifiers would have to be unique across a
run rather than within an arm, and every stored record, fixture, document, and test
from Phase 2 would move. It also leaves the sequence number, which discloses how many
memories an arm has ever held — a second, quieter signal about the mechanism.

## Decision

Models never see a memory identifier. Each request assigns its own labels, `m1`
through `mn`, in presentation order, and the mapping back to real identifiers lives
only as long as the response is being resolved. It is not stored and it is not
stable between requests.

Every model-facing schema names memories by label: `memory_ref`, `source_memory_refs`,
`cited_memory_refs`, `evidence_memory_refs`. The gateway resolves them, and a label
that was not in the request is rejected rather than resolved.

Because no identifier reaches a prompt, the blindness guard can then ban arm
identifiers, policy version strings, and mechanism vocabulary outright, anywhere in
a rendered request, without any legitimate use to make an exception for.

## Consequences

The stored record and the prompt now use different names for the same memory, and
the gateway is the only place that translates. That is one function, tested, and the
alternative — every call site mapping labels itself — is where an audit could be
attributed to the wrong memory and move the wrong statistic.

Labels also hide the sequence number. A model can see the order it was given
memories in, which is inherent, but not how many an arm has held or how long any of
them has survived.

A label is meaningless outside its request. Nothing may store one, and a
`MemoryPresentation` deliberately has no serialisation.

Phase 2's identifier format is unchanged, which keeps the ledger, the lineage, the
simulator, and every existing test intact.

## Revisit when

A protocol needs a model to refer to a memory across two requests — a multi-turn
interview, say. That would need labels stable for the exchange, which is a different
scheme, and it must still not be the real identifier.
