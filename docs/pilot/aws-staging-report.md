# AWS staging report

What the Phase 7 deployment actually did, in an account, with real models. Written
from executed commands and their output; anything not executed says so.

**Status: the stack is deployed and a real six-arm cycle commits. Canonical execution
is blocked on one thing, recorded in full below.**

## Deployment

|                   |                                                                    |
| ----------------- | ------------------------------------------------------------------ |
| Account           | `****2684` (masked; the full identifier is not recorded here)      |
| Caller            | IAM user `varad_personal_mac`, default credential chain            |
| Profile           | default (no `AWS_PROFILE` set; no key material in this repository) |
| Region            | `us-east-1`                                                        |
| Environment       | `staging`                                                          |
| Stack             | `AttentionSink-staging`                                            |
| Deployment commit | _see `git rev-parse HEAD` at the time of deploy_                   |

<!-- OUTPUTS -->

## Bedrock

| Role      | Model                          |
| --------- | ------------------------------ |
| Writer    | `amazon.nova-lite-v1:0`        |
| Auditor   | `amazon.nova-lite-v1:0`        |
| Judge     | `amazon.nova-lite-v1:0`        |
| Summary   | `amazon.nova-lite-v1:0`        |
| Embedding | `amazon.titan-embed-text-v2:0` |

Resolved from environment variables, never compiled in (ADR-006), and recorded
verbatim on the run configuration and in every export.

<!-- SMOKE -->

## The blocker for canonical execution

**Bedrock `CountTokens` is unavailable for every model this account can reach.**

Probed directly. All 50 on-demand text models in `us-east-1` return
`ValidationException: The provided model doesn't support counting tokens`, including
every Nova model. Every Anthropic inference profile the account can reach returns the
same, and so do `us-west-2`, `us-east-2`, `eu-central-1`, and `ap-northeast-1`.

This matters because ADR-011 makes the model's own tokenisation the production unit
for the active-memory budget **with no fallback**, and the cycle engine counts on
every cycle: the candidate memory's cost is measured before it is admitted. With no
counter there is no cycle.

[ADR-012](../adr/012-approximate-token-counts-in-staging.md) resolves it without
weakening the guarantee. `TOKEN_COUNT_SOURCE` **declares** the counter before the run
starts; the choice is recorded on `PilotRunConfiguration.token_count_source`, on
`TokenBudget.counter_version`, and in every export; and
`require_run_kind_consistent` refuses an `AWS_CANONICAL` run denominated in an
approximate one. There is still no fallback: `BedrockTokenCounter` raises when
`CountTokens` is unavailable and nothing catches it.

**Consequences for the canonical run.** It cannot start until `CountTokens` covers a
model the experiment can use in the deployment Region. The protocol therefore stays
`LOCAL_VALIDATED` rather than `AWS_CALIBRATED`, because calibrating a budget against a
counter the canonical run will not use would produce a number nobody should freeze.

## Remaining blockers for canonical execution

1. **`CountTokens` support** — above. The only hard one.
2. **The protocol is not frozen.** `promote_documents` refuses `AWS_CALIBRATED` and
   `FROZEN` while the budget is denominated in an approximate counter. Unblocked by (1).
3. **No canonical deployment exists.** `preflight` refuses
   `AS_DEPLOYMENT_ENVIRONMENT=production`, and `AwsSettings` refuses `AS_CANONICAL`
   outside it. Both are deliberate for this phase.
4. **The staging cycle ceiling is three.** Deliberate: it makes arming the scheduler by
   mistake cost three cycles rather than twenty-four. Production leaves it unset.
5. **Alarms have no notification channel.** They fire and are visible in CloudWatch;
   nothing is subscribed, because an address does not belong in this repository.
6. **`arm_summary` compression is unexercised against real models on AWS.** The
   staging run is short; the Dreamer path is proved locally over 24 cycles and by the
   summariser smoke test, not by a deployed compression.
