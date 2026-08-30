# Pilot Phase 4 - The canonical pilot protocol and a local six-arm cycle engine

Run under the Pilot Scope Override, which takes priority over conflicting
requirements in the original production-scale brief.

## Plan

- [x] Audit Phases 1-3 against the seven named incompatibilities before writing
      anything. Reuse what is already more capable than the pilot needs; configure
      unused capability off rather than deleting it.
- [x] `docs/pilot-scope.md` and `docs/adr/ADR-008-pilot-snapshot-architecture.md`.
- [x] `experiments/pilot/` - five machine-readable protocol files plus predictions,
      each carrying a schema version, protocol version, status, title, description,
      creation time, and a content digest written by the freezing command.
- [x] Twelve Station Kestrel seed memories; twenty-four stimuli across five phases;
      ten checkpoint questions with the first six scoring factual recall.
- [x] `PilotRunConfiguration`, `DRAFT`/`FROZEN`/`RETIRED`, and commands that
      validate, calibrate, freeze, and detect a file edited after freezing.
- [x] `packages/pilot` - the persistence-independent cycle engine, the model-call
      budget, the snapshot types, and the local export.
- [x] `make pilot-validate`, `pilot-calibrate`, `pilot-freeze`, `pilot-local-cycle`,
      `pilot-local-run`, `pilot-local-export`.
- [x] Tests for all twenty-one listed subjects: unit, property, and integration.
- [x] `make verify` green, per-package coverage gate green, and
      `docs/implementation-status.md` updated.

## Review

**Six of the seven named incompatibilities were already satisfied; nothing was
rebuilt.** The audit is in `docs/implementation-status.md`. The two that needed a
decision rather than a patch were the Dreamer and the auditor.

**The Dreamer turned out to be the summarising arm, not a missing role.** Before the
Pilot Scope Override arrived, the audit read "missing Dreamer lineage" as a new model
role that the domain structurally could not support: `Memory` forbids
`parent_memory_ids` on anything that is not a `SUMMARY`. The override settled it -
`arm_summary` _is_ the Dreamer, and its lineage is the existing summary lineage. The
domain needed no change at all, and the pilot's `dreamer` protocol block maps
one-to-one onto `SummarizationConfig`.

**The auditor was never a Phase 1-3 problem.** Nothing sequenced a cycle before this
phase, so "mandatory citation-auditor calls on every cycle" could only become a Phase
4 design constraint. `citation_mode: claimed_validated` validates a claim
structurally - it exists, it is active, duplicates collapse - and records
`auditor_version: claimed.validated-v1` so nothing downstream mistakes it for an
audited citation. `CitationMode.AUDITED` exists and the engine refuses it rather than
silently downgrading.

**The two-stage compression contract needed the caller to commit between rounds.**
`finalize_compression` reserves the identifier the _next_ free creation slot will
take, so a second compression round in one cycle only lines up if the caller applies
the first decision to the state before writing the second summary. The engine's
rebalance loop therefore applies each decision at the top of the iteration rather
than only at the end. Getting this wrong would have lost the first summary silently.

**A property test found a real defect.** `validate_claims` constructed a
`RejectedClaim` whose `memory_id` carried the domain's identifier constraint, so a
malformed claim raised a `ValidationError` from inside the code that was supposed to
_reject_ it. The field is now a plain bounded string: it records what was claimed,
and a claim can be malformed.

**Three pieces of code were deleted rather than covered.** `PilotEngine.states` had
no caller; `_require_active` and `_require_known` were guards that could not fire,
because their inputs come from a decision the same state just produced. A
`pinned_origin_memory_id` emptiness check in `PilotRunConfiguration` was unreachable
for the same reason - the field's own constraint already forbids it. Unreachable
safety code is not safety, and covering it would have meant writing tests that
asserted nothing.

**The pin is resolved for one arm, not flagged on the memory.** Setting `pinned=True`
on the seed would have protected it in _every_ arm, because every mechanism refuses to
retire a pinned memory - and the arms would then differ in something other than
mechanism. The protocol names a _seed_; the engine resolves it to the arm-scoped
identifier only `arm_sink`'s mechanism reads.

**The ADR number collides deliberately.** The override names the record
`ADR-008-pilot-snapshot-architecture.md` and this repository already has an ADR-008
about budget token accounting. Renumbering an accepted ADR would break every existing
citation of it, so both keep their number and the `-pilot` suffix keeps them citable
apart. The collision is stated at the top of the new record and in the ADR index.

**One environment defect, not caused by this work.** Every file in
`.venv/lib/python3.12/site-packages` carries the macOS `UF_HIDDEN` flag, and CPython
3.12's `site.addpackage` skips hidden `.pth` files - so the editable install's path
file is silently ignored and `import attention_sink` fails. `chflags -R nohidden
.venv` clears it. It recurs when `uv` rewrites the venv. Nothing in the repository was
changed for it, because the fix belongs in the environment rather than in a
`pythonpath` setting that would mask a genuinely broken install.
