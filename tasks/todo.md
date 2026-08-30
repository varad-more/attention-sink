# Pilot Phase 6 - the local exhibition and the release candidate

Local-First Remaining-Phases Override binding. No AWS credentials, no AWS calls, no
AWS resources.

## Plan

- [x] 1. Validated frontend configuration; production-like builds fail without it.
- [x] 2. Seven routes.
- [x] 3. Landing page: premise, badges, run and analysis status, six cards, links.
- [x] 4. Six Minds: cards, cycle selector, focus mode, comparison table, evidence.
- [x] 5. Graveyard: every retirement, six filters, five sorts, state in the URL.
- [x] 6. Memory detail and lineage, with no prompt text anywhere.
- [x] 7. Timeline: accessible SVG, keyboard scrubber, table carrying the same figures.
- [x] 8. Interviews: checkpoint and question selectors, six answers side by side.
- [x] 9. Graveyard Echo: both texts, both similarities, the delta, careful language.
- [x] 10. Methodology, including all eight required limitations.
- [x] 11. Polling that pauses when hidden and never moves a pinned cycle.
- [x] 12. Accessibility: landmarks, focus, labels, text alternatives, contrast.
- [x] 13. `make pilot-local-demo`, `-build`, `-e2e`, `-release-check`.
- [x] 14. Fourteen Playwright flows, desktop and mobile.
- [x] 15. Release readiness and requirements traceability.
- [x] 16. Local release artifacts, all labelled.

## Review

**The suites found two real defects that review had not.**

The first was mine, from Phase 5: one SQLite connection shared across Starlette's
threadpool. The Phase 5 code carried a comment arguing `check_same_thread=False` was
safe because writes were serialised. That reasoning was wrong - the flag silences the
thread check but does not make a connection re-entrant - and the first Playwright run
produced `sqlite3.InterfaceError: bad parameter or other API misuse`. Fixed with one
connection per thread. The comment that defended it has been replaced with one that
says what actually happened.

The second was an accessibility failure that would have shipped: every route returned
its `h1` only after data loaded, so a slow or failed load left the page with no
heading at all - worst exactly when a reader most needs to know where they are. Every
route now renders its heading first.

A third problem was found while wiring the exhibition: the browser was receiving 200s
and discarding them, because the frontend runs on a different port and the API had no
CORS headers. An explicit origin list rather than a wildcard - the API is read-only,
but read-only is not a reason to let any page on the internet read a run.

**The public names live in exactly one file.** `apps/web/src/arms.ts` is the only
place "Goldfish" or "Dreamer" appears. Not the protocol, not the database, not an API
response, not a prompt. A test asserts each `arm_id` does not contain its own public
name, because that is the shape the leak would take.

**Two API additions were needed and one was a shortcut being repaid.** The exhibition
needs echo texts and contradiction classifications, which cost embeddings and
sometimes a model call to produce, so they are stored rather than recomputed per
request - a new `analysis_artifacts` table and migration 2. The divergence route had
been reading from the `embeddings` table in Phase 5, which was a misuse of a table
named for something else; it now reads the artifact.

**Question scores had no arm.** `QuestionScore` recorded what matched but not who
answered, so the interview view could not attribute a score to a mind. Caught by an
eslint rule flagging a condition I had written to paper over it. `arm_id` and `cycle`
are now on the record.

**Nothing is FAIL or PARTIAL.** Everything deferred is deferred because it needs an
AWS account, and each deferred item has a local adapter standing in for it today.
