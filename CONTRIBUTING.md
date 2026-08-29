# Contributing

## Setup

```bash
make bootstrap
```

Installs Python dependencies with `uv`, Node dependencies with `npm`, and the
pre-commit hooks. Requires Python 3.12, Node 20+, and `uv`. It does not require AWS
credentials, and nothing in the local development loop ever will.

## The loop

```bash
make format      # rewrite to project style
make verify      # exactly what CI runs, in CI's order
```

`make verify` is the contract. If it passes locally and fails in CI, that is a bug in
the Makefile or the workflow, not an accepted cost of doing business.

Narrower targets exist for faster iteration: `make test-unit`, `make test-property`,
`make test-integration`, `make test-web`, `make synth`.

## Rules that are not style preferences

These come from the experimental design. Breaking one invalidates results rather
than merely making the code worse.

1. **The writer model must stay blind.** It may not learn its memory policy, its
   public name, any prediction about it, any metric it scored, or the state of any
   other arm. Public arm names never enter a generation, audit, summarisation, or
   evaluation prompt.
2. **Policy code decides what is forgotten, not a model.** A model may write a
   summary within a budget the policy set; it may not choose what to compress.
3. **Evicted memories do not come back.** Only a separately recorded external event
   may reintroduce equivalent information.
4. **Interviews and evaluations are read-only.** Their output never becomes an
   agent's memory.
5. **A committed cycle is immutable.** Community interaction and causal forks use
   separate run IDs. No canonical result is ever edited by hand.
6. **Every nondeterministic decision is reproducible from logged state.** Random
   eviction uses an application-controlled seed that is recorded. Model generations
   are recorded for exact playback, which is not the same as being regenerable.
7. **Raw chain-of-thought is never requested, stored, logged, or displayed.**
8. **Simulated output is labelled everywhere it appears**, and can never be
   canonical.

## Dependency direction

`packages/domain` and `packages/policies` are pure: standard library and Pydantic
only. They may not import `boto3`, Strands, Lambda utilities, or frontend code.
Adapters implement protocols the domain defines, never the reverse. This is checked
by `tests/unit/test_import_boundaries.py`, not left to review.

## Architecture decisions

Any material deviation from the documented architecture needs an ADR in
`docs/adr/`. Copy the shape of an existing one: context, decision, consequences,
and what would make us revisit it.

## Commits and pull requests

Small and reviewable. A pull request should say what invariant it preserves or what
it changes, and include the output of the commands it claims to have run.
