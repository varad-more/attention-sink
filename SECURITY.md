# Security policy

## Reporting a vulnerability

Report suspected vulnerabilities privately through GitHub's "Report a vulnerability"
flow on this repository's Security tab. Please do not open a public issue for
anything exploitable. Include the affected component, reproduction steps, and impact.

Expect an acknowledgement within three working days.

## Credentials

This project never stores credentials in source, in `.env.example`, or in
configuration. AWS access uses the default credential chain: SSO, an instance role,
or a named profile. There is no access-key variable anywhere in the codebase, and
adding one should fail review.

`gitleaks` runs as a pre-commit hook and again in CI. A finding blocks the merge.

## Trust boundaries

- **Visitor-submitted text is data, never instruction.** It is length-limited,
  validated, and never interpolated into a prompt in a position where it could be
  read as direction to a model.
- **Raw visitor submissions and authorization tokens are never logged.**
- **Administrative actions require authentication** and are isolated from the
  canonical run.
- **Internal prompts are not exposed through public APIs.** Prompt hashes are
  published for verification; prompt text is not.
- **S3 buckets are private.** The web client is served through CloudFront with Origin
  Access Control.
- **CORS is restricted** to configured frontend origins, and a Content Security
  Policy plus standard security headers are applied at the edge.
- **Write APIs are throttled and rate-limited**, behind a budget circuit breaker.

Several of the controls above describe infrastructure that later phases build. See
[docs/implementation-status.md](docs/implementation-status.md) for what exists today;
that file is kept honest deliberately, because a security policy that describes
aspirations as facts is worse than none.

## Runtime modes

`local` mode serves deterministic fixtures, requires no credentials, and marks all
output as simulated. `production` mode fails closed: it refuses to start without a
Region and every model identifier, so a misconfigured deployment cannot silently
serve fixtures as real results.
