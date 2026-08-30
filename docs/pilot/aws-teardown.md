# AWS teardown

Everything this project created in AWS, and how to remove it. Kept as a checklist
rather than as prose because the cost of missing one line is a resource nobody is
looking at that bills every month.

**Account:** `****2684` &nbsp;&nbsp; **Region:** `us-east-1` &nbsp;&nbsp;
**Profile:** default credential chain

Two stacks exist, and they are not the same to delete.

| Stack                      | Holds                                          | Safe to delete           |
| -------------------------- | ---------------------------------------------- | ------------------------ |
| `AttentionSink-staging`    | `run_aws_staging`, three cycles, no results    | yes, nothing is a result |
| `AttentionSink-production` | `run_aws_canonical`, the canonical twenty-four | **only deliberately**    |

The canonical run is the experiment. Its dataset is exported to the production export
bucket and can be copied out before anything is deleted; once the table and that
bucket are gone the run cannot be reproduced without running it again, which would be
a different run under the same protocol.

## The short version

```bash
make aws-destroy AWS_ENV=staging          # staging, in one command

# Production refuses, deliberately. To remove it anyway:
aws s3 sync s3://attentionsink-production-exportbucket4e99310e-gyohrcdchg5s ./canonical-dataset
aws dynamodb update-table --no-deletion-protection-enabled \
  --table-name AttentionSink-production-PilotTable82A5350E-1NF2WP04HP23S
cd infrastructure/cdk && npx cdk destroy AttentionSink-production -c environment=production
# RemovalPolicy.RETAIN keeps the table and both buckets behind: delete them by name.
```

`cdk destroy` removes every resource in the table below **in staging**, because that
environment sets `RemovalPolicy.DESTROY` and `autoDeleteObjects` on both buckets.
Production retains its table and buckets on purpose, so those three survive the stack
and have to be deleted by name. Two further things neither environment removes, because
they are shared with anything else you deploy in this account, are listed under "Left
behind on purpose".

## A deploy disarms a running experiment

`cdk deploy` restores `AS_EXECUTION_ENABLED=false` from the template, because every
environment deploys inert. That is the right default and it is also a trap: deploying
while a run is advancing stops it silently, and the schedule keeps firing into a
function that refuses. The log says `result_code: execution_disabled` on every fire.

Re-arm afterwards:

```bash
make aws-execution-enable AWS_ENV=production
```

This happened once during the canonical run, between cycles 8 and 9. Nothing was lost
— a refused fire commits nothing — and the run continued from where it was.

## Cheapest way to stop the spend without deleting anything

```bash
make aws-schedule-disable AWS_ENV=production     # nothing asks for a cycle
make aws-execution-disable AWS_ENV=production    # nothing would run one if asked
```

Both are already off. With the schedule disabled and the function disarmed, the stacks
make no model calls at all and cost only storage: a few megabytes of DynamoDB and S3,
and the CloudFront distribution's idle footprint.

## What the stack creates

| Kind           | Logical id                   | What it is                                    | Removed by `cdk destroy` (staging / production) |
| -------------- | ---------------------------- | --------------------------------------------- | ----------------------------------------------- |
| DynamoDB table | `PilotTable`                 | One table, `PK`/`SK`, plus the `GSI1` index   | yes / **no, RETAIN + deletion protection**      |
| Lambda         | `RunCycleFunction`           | Advances the run by one cycle                 | yes                                             |
| Lambda         | `AnalysisFunction`           | Scores a committed cycle                      | yes                                             |
| Lambda         | `ReadApiFunction`            | Serves the public read API                    | yes                                             |
| Lambda         | `CustomS3AutoDeleteObjects…` | CDK's bucket-emptying helper                  | yes                                             |
| Lambda         | `CustomCDKBucketDeployment…` | CDK's frontend-upload helper                  | yes                                             |
| Lambda layer   | `…AwsCliLayer`               | Used by the bucket deployment helper          | yes                                             |
| Log group      | `RunCycleFunctionLogs`       | 30 days staging, 365 days production          | yes                                             |
| Log group      | `AnalysisFunctionLogs`       | 30 days staging, 365 days production          | yes                                             |
| Log group      | `ReadApiFunctionLogs`        | 30 days staging, 365 days production          | yes                                             |
| API Gateway    | `ReadApi`                    | HTTP API, one `GET /{proxy+}` route           | yes                                             |
| S3 bucket      | `FrontendBucket`             | Private, holds the exhibition                 | yes, emptied first / **no, RETAIN**             |
| S3 bucket      | `ExportBucket`               | Private, versioned, holds datasets            | yes, emptied first / **no, RETAIN**             |
| CloudFront     | `Distribution`               | The public entry point                        | yes                                             |
| CloudFront     | `SecurityHeaders`            | Response headers policy (CSP, HSTS)           | yes                                             |
| CloudFront     | OAC                          | Origin access control for the frontend bucket | yes                                             |
| EventBridge    | `CycleSchedule`              | `attention-sink-<env>-cycle`, **disabled**    | yes                                             |
| EventBridge    | `CycleCompletedRule`         | Routes `CycleCompleted` to analysis           | yes                                             |
| SQS            | `SchedulerDlq`               | Dead letters from the schedule                | yes                                             |
| SQS            | `AnalysisDlq`                | Dead letters from analysis                    | yes                                             |
| IAM            | 6 roles + 5 policies         | One role per function, one for the scheduler  | yes                                             |
| CloudWatch     | 9 alarms                     | Failed cycle, DLQs, errors, limits, silence   | yes                                             |
| CloudWatch     | 4 metric filters             | Derived from the structured logs              | yes                                             |

## Left behind on purpose

Neither belongs to this project, and deleting either would break every other CDK
deployment in the account.

| What                | Where                                         | Remove with                                               |
| ------------------- | --------------------------------------------- | --------------------------------------------------------- |
| CDK bootstrap stack | CloudFormation stack `CDKToolkit`             | `aws cloudformation delete-stack --stack-name CDKToolkit` |
| CDK asset bucket    | `cdk-hnb659fds-assets-539247472684-us-east-1` | empty it, then delete the bootstrap stack                 |
| CDK asset ECR repo  | `cdk-hnb659fds-container-assets-…`            | deleted with the bootstrap stack                          |

Only remove these if this account has no other CDK application.

## Deployed names

### `AttentionSink-production` — the canonical run

| Resource           | Name                                                                |
| ------------------ | ------------------------------------------------------------------- |
| Table              | `AttentionSink-production-PilotTable82A5350E-1NF2WP04HP23S`         |
| Frontend bucket    | `attentionsink-production-frontendbucketefe2e19c-x4zgsdqk37gz`      |
| Export bucket      | `attentionsink-production-exportbucket4e99310e-gyohrcdchg5s`        |
| Run-cycle function | `AttentionSink-production-RunCycleFunctionBD273DDC-ZbZNOlFXnwP1`    |
| Analysis function  | `AttentionSink-production-AnalysisFunction3223EF84-AzwMwAEQc7yR`    |
| Read-API function  | `AttentionSink-production-ReadApiFunction4CF3F8B1-thIvMiw98jnr`     |
| Schedule           | `attention-sink-production-cycle`                                   |
| Scheduler DLQ      | `AttentionSink-production-SchedulerDlqBE0A9ADA-YbKbSbN4liQh`        |
| Analysis DLQ       | `AttentionSink-production-AnalysisDlqF1946B7D-hgTDw7fcmT4m`         |
| API                | `https://ioyvs8o9xa.execute-api.us-east-1.amazonaws.com`            |
| CloudFront         | `https://d1qskxceo899me.cloudfront.net`                             |
| Log groups         | `AttentionSink-production-{RunCycle,Analysis,ReadApi}FunctionLogs*` |

### `AttentionSink-staging` — Phase 7, superseded

| Resource           | Name                                                             |
| ------------------ | ---------------------------------------------------------------- |
| Table              | `AttentionSink-staging-PilotTable82A5350E-TUG8RU4MJPY6`          |
| Frontend bucket    | `attentionsink-staging-frontendbucketefe2e19c-xa1po2mj3f0s`      |
| Export bucket      | `attentionsink-staging-exportbucket4e99310e-0wyoe4xvbcli`        |
| Run-cycle function | `AttentionSink-staging-RunCycleFunctionBD273DDC-XnLclEulbtHe`    |
| Analysis function  | `AttentionSink-staging-AnalysisFunction3223EF84-59eKdXoQlWaC`    |
| Read-API function  | `AttentionSink-staging-ReadApiFunction4CF3F8B1-nb27v54hrmAa`     |
| Schedule           | `attention-sink-staging-cycle`                                   |
| Scheduler DLQ      | `AttentionSink-staging-SchedulerDlqBE0A9ADA-hr2rV48eZHy8`        |
| Analysis DLQ       | `AttentionSink-staging-AnalysisDlqF1946B7D-frXBaBCdq61c`         |
| API                | `https://tjsf7uniy6.execute-api.us-east-1.amazonaws.com`         |
| CloudFront         | `https://d1t3b02jxd27xb.cloudfront.net`                          |
| Log groups         | `AttentionSink-staging-{RunCycle,Analysis,ReadApi}FunctionLogs*` |

The staging stack's run cannot be read by the current code: `ModelCallLimits` gained a
required field in Phase 8, and `run_aws_staging`'s stored configuration predates it.
That is a superseded run rather than lost data — nothing in it was a result — but it
means `make aws-status AWS_ENV=staging` now fails, and the honest fix for staging is
to delete it rather than to migrate it.

## Verify nothing is left

```bash
aws cloudformation describe-stacks --stack-name AttentionSink-staging      # should fail
aws cloudformation describe-stacks --stack-name AttentionSink-production   # should fail
aws dynamodb list-tables --region us-east-1
aws s3 ls | grep attentionsink
aws lambda list-functions --region us-east-1 --query "Functions[?starts_with(FunctionName,'AttentionSink')].FunctionName"
aws scheduler list-schedules --region us-east-1
aws sqs list-queues --region us-east-1 --queue-name-prefix AttentionSink
aws logs describe-log-groups --region us-east-1 --log-group-name-prefix /aws/lambda/AttentionSink
aws cloudwatch describe-alarms --region us-east-1 --alarm-name-prefix AttentionSink
```

## If `cdk destroy` refuses

The two things that block it, and what to do:

- **A bucket is not empty.** `autoDeleteObjects` is on in staging, so this only
  happens if the helper function was removed first. Empty it by hand:
  `aws s3 rm s3://<bucket> --recursive` — and note the export bucket is versioned, so
  use `aws s3api delete-objects` with a version listing, or set a lifecycle rule.
- **The table has deletion protection.** Off in staging, on in production. Turn it
  off with
  `aws dynamodb update-table --table-name <name> --no-deletion-protection-enabled`.

## Nothing outside the stack

No Bedrock resource is created: model access is a property of the account, not
something this project provisions. No secret, parameter, KMS key, VPC, NAT gateway, or
Route 53 record is created either. The only spend outside the stack is the Bedrock
invocations themselves, which are per-call and stop when the run stops.
