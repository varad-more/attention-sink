# Security summary

Every line here corresponds to a row in
[`final-requirements-traceability.md`](https://github.com/varad-more/attention-sink/blob/main/docs/pilot/final-requirements-traceability.md),
where the evidence is a test or a command that would fail if the property stopped
holding.

## Storage

Both S3 buckets are private, with all four public-access blocks enabled. A direct object
GET returns 403. CloudFront reaches them through Origin Access Control, and a CDK
assertion walks every origin on the distribution and permits nothing but S3 behind an
OAC. The distribution serves only the `canonical/` prefix of the export bucket, so a
staging rehearsal is never reachable as though it were a result.

## The public API

Read-only by construction rather than by policy. No mutating verb is registered, a test
asserts the route table contains only GET, and POST, PUT, PATCH and DELETE all return
404 against the deployed API. The read Lambda's IAM role holds no write action at all,
so the guarantee survives a routing mistake.

## Headers and origins

Every response carries a restrictive Content-Security-Policy, HSTS, `X-Content-Type-Options:
nosniff`, `X-Frame-Options: DENY` and `Referrer-Policy: no-referrer`. CORS names the
distribution, never `*`.

## Credentials

The default credential chain, always. No key material in the repository, in
`.env.example`, or in any documentation. No model identifier and no Region is compiled
into domain code — both come from configuration, and a production runtime refuses to
start without them.

## Logging

A closed thirteen-field allowlist. No prompt, no generated text, no visitor input and no
authorization token can reach CloudWatch, because a field that is not on the list is not
emitted. Raw chain-of-thought is never requested, stored, logged or displayed; the
structured output schemas carry no reasoning field.

## Input handling

Visitors submit nothing in pilot v1 — there is no write path for them to reach. All
request bodies and path parameters are validated. Stimulus and memory text is treated as
data throughout: it is never interpolated into an instruction position, and the memory
labels a prompt sees are opaque identifiers
([ADR-010](https://github.com/varad-more/attention-sink/blob/main/docs/adr/010-opaque-memory-labels-in-prompts.md)).

## Spending and abuse

Model calls are capped per cycle (six writers) and per run (600), checked before every
call, with a CloudWatch alarm on the limit. Reserved concurrency of 1 on the run-cycle
function and 100 on the read API. A burst of visitors meets the cap and gets a stated
error rather than an unbounded bill.

## Administrative actions

Arming a deployment means changing a Lambda function's environment, which needs IAM
permission. Nothing public can do it, and a `cdk deploy` restores the disarmed default
rather than preserving an armed one. Both the schedule and the function's own switch have
to be on for a cycle to happen.

## Immutability

Canonical snapshots are written under `attribute_not_exists` and hashed. A committed
result cannot be rewritten by a retry, by a later deploy, or by hand. The protocol is
frozen by digest: editing a protocol file makes the next run refuse to start, and
`make pilot-freeze` refuses to rewrite a manifest that already exists.

## What is masked in this package

The AWS account identifier appears nowhere in full — it is `****2684` in the
architecture diagram, the deployment evidence and the teardown guide. No complete ARN, no
log line and no request token appears in any published asset.

## Reporting

[SECURITY.md](https://github.com/varad-more/attention-sink/blob/main/SECURITY.md).
