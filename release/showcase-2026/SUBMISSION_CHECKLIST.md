# Submission checklist

State as of the last run of `scripts/build_release_package.py`. Anything ticked here is
ticked because something in this repository verifies it, not because it looked right.

## Qualification

- [x] A working application, deployed on AWS, publicly reachable — https://d1qskxceo899me.cloudfront.net returns HTTP 200
- [x] Uses an AWS generative AI service — Amazon Bedrock, `amazon.nova-micro-v1:0` and `amazon.titan-embed-text-v2:0`
- [ ] **Source publicly available** — `https://github.com/varad-more/attention-sink` currently answers HTTP 404 to a logged-out visitor. The repository is private. Make it public before submitting: `gh repo edit varad-more/attention-sink --visibility public --accept-visibility-change-consequences`. Both the README and the article link it as public source.
- [x] Article drafted with the required title and `#application` tag
- [ ] **Article published** — see `ARTICLE_PUBLICATION_CHECKLIST.md`.

**Two outstanding items, both above.** The repository is private and the article is
unpublished. Everything else in this file is verified by something that would fail if it
stopped holding.

## Evidence the application works

- [x] 24 of 24 cycles committed on the canonical run
- [x] 144 snapshots, 157 graveyard records, 2,062 metric rows, 18 interviews
- [x] 95 scheduler-triggered Lambda invocations, 0 errors
- [x] All screenshots taken from the deployed CloudFront site against `run_aws_canonical`
- [x] No fixture data anywhere in the showcase — the capture script refuses it
- [x] Dataset published and verifiable with `sha256sum -c`, using nothing from the repository

## Scientific honesty

- [x] Predictions registered before the run and published verbatim in every export
- [x] All eight graded, including two failures and two that cannot be decided
- [x] The result that most limits the conclusions (the random control) stated prominently
- [x] n=1 stated in the README, the article, the site, and every chart footer
- [x] The KV-cache disclaimer stated before any claim, in all four places
- [x] Every metric published with its evidence and its limitation

## Engineering

- [x] `make verify` passes: lint, typecheck, tests, CDK synth
- [x] 26 invariant checks pass against both the canonical run and a local run
- [x] Playwright: 66 passed, 2 skipped, against the deployed site
- [x] Per-package coverage gates at 95%, all passing
- [x] No TODO, FIXME, placeholder handler, or commented-out implementation

## Security and cost

- [x] Both S3 buckets private; CloudFront over Origin Access Control
- [x] Public API registers no mutation route
- [x] No credential in the repository, in any example file, or in any log
- [x] Model spending capped per cycle and per run
- [x] Scheduler disabled and execution switch off; the deployment costs only storage
- [x] Account identifier masked in every published asset

## Before you submit

1. Make the repository public. The validator fails until it is.
2. Publish the article and record its URL (`ARTICLE_PUBLICATION_CHECKLIST.md`).
3. Put that URL in the README and in `release-manifest.json`.
4. Re-run `uv run python scripts/validate_showcase_content.py` — it should report zero blocking failures.
5. Re-run `uv run python scripts/build_release_package.py`.
6. Commit.
