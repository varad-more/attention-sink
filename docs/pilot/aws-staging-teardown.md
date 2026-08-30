# AWS staging teardown

Everything Phase 7 created in AWS, and how to remove it. Kept as a checklist rather
than as prose because the cost of missing one line is a resource nobody is looking at
that bills every month.

**Account:** `****2684` &nbsp;&nbsp; **Region:** `us-east-1` &nbsp;&nbsp;
**Profile:** default credential chain &nbsp;&nbsp; **Environment:** `staging`

Nothing below holds a canonical result. Every artefact is `AWS_STAGING`,
non-canonical, and safe to delete.

## The short version

```bash
make aws-destroy AWS_ENV=staging      # everything in the stack, in one command
```

`cdk destroy` removes every resource in the table below, because the staging
environment sets `RemovalPolicy.DESTROY` and `autoDeleteObjects` on both buckets. Two
things it does **not** remove, because they are shared with anything else you deploy
in this account, and both are listed under "Left behind on purpose".

## What the stack creates

| Kind           | Logical id                   | What it is                                    | Removed by `cdk destroy` |
| -------------- | ---------------------------- | --------------------------------------------- | ------------------------ |
| DynamoDB table | `PilotTable`                 | One table, `PK`/`SK`, plus the `GSI1` index   | yes                      |
| Lambda         | `RunCycleFunction`           | Advances the run by one cycle                 | yes                      |
| Lambda         | `AnalysisFunction`           | Scores a committed cycle                      | yes                      |
| Lambda         | `ReadApiFunction`            | Serves the public read API                    | yes                      |
| Lambda         | `CustomS3AutoDeleteObjects…` | CDK's bucket-emptying helper                  | yes                      |
| Lambda         | `CustomCDKBucketDeployment…` | CDK's frontend-upload helper                  | yes                      |
| Lambda layer   | `…AwsCliLayer`               | Used by the bucket deployment helper          | yes                      |
| Log group      | `RunCycleFunctionLogs`       | 30-day retention in staging                   | yes                      |
| Log group      | `AnalysisFunctionLogs`       | 30-day retention in staging                   | yes                      |
| Log group      | `ReadApiFunctionLogs`        | 30-day retention in staging                   | yes                      |
| API Gateway    | `ReadApi`                    | HTTP API, one `GET /{proxy+}` route           | yes                      |
| S3 bucket      | `FrontendBucket`             | Private, holds the exhibition                 | yes, emptied first       |
| S3 bucket      | `ExportBucket`               | Private, versioned, holds datasets            | yes, emptied first       |
| CloudFront     | `Distribution`               | The public entry point                        | yes                      |
| CloudFront     | `SecurityHeaders`            | Response headers policy (CSP, HSTS)           | yes                      |
| CloudFront     | OAC                          | Origin access control for the frontend bucket | yes                      |
| EventBridge    | `CycleSchedule`              | `attention-sink-staging-cycle`, **disabled**  | yes                      |
| EventBridge    | `CycleCompletedRule`         | Routes `CycleCompleted` to analysis           | yes                      |
| SQS            | `SchedulerDlq`               | Dead letters from the schedule                | yes                      |
| SQS            | `AnalysisDlq`                | Dead letters from analysis                    | yes                      |
| IAM            | 6 roles + 5 policies         | One role per function, one for the scheduler  | yes                      |
| CloudWatch     | 9 alarms                     | Failed cycle, DLQs, errors, limits, silence   | yes                      |
| CloudWatch     | 4 metric filters             | Derived from the structured logs              | yes                      |

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

## What it holds right now

One run, `run_aws_staging`: `AWS_STAGING`, non-canonical, three committed cycles, 18
snapshots, 6 interviews, 180 metric rows, and two dataset exports in the export
bucket. Nothing here is a result and nothing needs keeping.

The run-cycle function is **disarmed** (`AS_EXECUTION_ENABLED=false`) and the schedule
is **DISABLED**, so the stack costs storage and nothing else while it sits there.

## Verify nothing is left

```bash
aws cloudformation describe-stacks --stack-name AttentionSink-staging   # should fail
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
