# Architecture decision records

One file per decision that would be expensive or misleading to reverse silently.
Each records the context at the time, the decision, its consequences, and what would
make us revisit it. Superseded records stay in place; they are not deleted.

| ADR                                                                   | Decision                                                       |
| --------------------------------------------------------------------- | -------------------------------------------------------------- |
| [001](001-application-level-memory.md)                                | Study application-level memory, not the model's internal state |
| [002](002-event-ledger-and-projections.md)                            | Immutable event ledger plus mutable projections                |
| [003](003-step-functions-standard-workflow.md)                        | Step Functions Standard Workflow with a six-arm inline map     |
| [004](004-policy-blind-writer-and-evaluator.md)                       | The writer and the evaluator are blind to the policy           |
| [005](005-immutable-canonical-run-and-forks.md)                       | The canonical run is immutable; exploration happens in forks   |
| [006](006-model-ids-from-configuration.md)                            | Model identifiers and Region come from configuration           |
| [007](007-python-backend-typescript-infrastructure-react-frontend.md) | Python backend, TypeScript infrastructure, React frontend      |
| [008](008-budget-token-accounting.md)                                 | The budget is denominated in versioned budget tokens           |
| [009](009-two-stage-compression.md)                                   | Compression is policy-planned and model-written, in two stages |
| [010](010-opaque-memory-labels-in-prompts.md)                         | Models see memories under opaque per-request labels            |
| [011](011-exact-token-counts-in-production.md)                        | Production budgets use the model's own token counts            |
