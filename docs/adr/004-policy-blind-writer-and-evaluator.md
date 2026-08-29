# ADR-004: The writer and the evaluator are blind to the policy

Status: accepted, 2026-08-29.

## Context

Every arm is generated and judged by a language model. Language models are
exquisitely sensitive to framing. If a writer prompt said "you are the agent who
forgets everything", the resulting text would perform forgetfulness rather than
exhibit it, and the experiment would measure the prompt instead of the mechanism.
The same hazard applies to a judge told which arm it is scoring, which would then be
scoring its own expectations.

This failure mode is quiet. Nothing crashes; the numbers merely stop meaning what
they appear to mean.

## Decision

No prompt used for generation, citation auditing, summarisation, or evaluation may
contain:

- the arm's public display name,
- the name or description of its memory policy,
- any prediction about how it should behave,
- any metric it has scored,
- the state, memories, or output of any other arm.

Internally, arms are referred to only by neutral identifiers (`arm_fifo`,
`arm_lru`, `arm_heavy`, `arm_sink`, `arm_random`, `arm_summary`). Public names exist
solely in the presentation layer, downstream of everything the model sees.

A fresh, stateless writer agent is constructed for every generation, so that no
state leaks between arms or between cycles through a reused client.

Policy decisions are made by deterministic code, never by a model. The
summarisation arm is the closest call, and the split is explicit: the policy chooses
which memories are compressed and the maximum size of the result; the model is asked
only to write within that budget.

## Consequences

- The writer sees a stimulus and a list of active memories. Nothing else. The only
  thing that differs between arms is which memories are in that list, which is
  exactly the independent variable.
- Prompt construction cannot take the arm as a formatting parameter, and prompt
  templates are stored as versioned files rather than assembled across handlers,
  so a leak would have to be deliberate.
- Evaluation prompts must be written to score text on its own terms. Any metric that
  needs to know the arm is computed from stored state by ordinary code, not by a
  judge.
- Presentation code needs a name-mapping layer at the boundary. That is a small,
  contained cost paid once.

## Revisit when

Never, while the six arms are being compared with each other. A separate study that
deliberately manipulates framing would be a different experiment with a different
run identifier.
