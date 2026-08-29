# ADR-006: Model identifiers and Region come from configuration

Status: accepted, 2026-08-29.

## Context

The model that wrote a thought is part of the experimental apparatus, exactly like
the prompt and the token budget. A model identifier compiled into a handler is an
experimental parameter that nobody recorded and nobody can see. When the default
silently changes - a new model generation, a copy-pasted constant, a Region with
different availability - two runs become incomparable while continuing to look
identical.

Regions carry the same problem plus a portability one: a hardcoded Region cannot be
deployed anywhere else, and reveals where the account lives.

## Decision

No model identifier and no Region appears as a literal in domain, policy, or service
code. Both come from the environment:

`WRITER_MODEL_ID`, `AUDITOR_MODEL_ID`, `JUDGE_MODEL_ID`, `SUMMARY_MODEL_ID`,
`EMBEDDING_MODEL_ID`, and `AWS_REGION`.

The resolved values are written into every run manifest. Two runs whose manifests
differ in any of these fields are different experiments, whatever else they share.

Resolution fails closed. In production mode a process with any of these unset
refuses to start. In local mode none of them is required, because local mode serves
fixtures and marks its output as simulated - the one case where the absence of a
model is not an error.

Sample values live in `.env.example`, where they are visibly configuration.
Credentials never do: AWS access uses the default credential chain.

## Consequences

- Changing the writer model is a deployment-configuration change with a visible
  audit trail, not a code change that reviewers skim past.
- Every published result can name the models that produced it.
- A misconfigured production deployment stops rather than degrading into fixtures,
  which is the failure mode that would be hardest to detect after the fact.
- Local development is possible with no AWS account at all.

## Revisit when

The set of model roles changes. Adding a role means adding a variable, a manifest
field, and a fail-closed check; it does not mean adding a default.
