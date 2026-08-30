/**
 * Everything Attention Sink runs on, in one stack.
 *
 * One table, three functions, one HTTP API, one schedule, one rule, two queues, two
 * buckets, one distribution. A single stack rather than several because the whole
 * deployment is created and destroyed together and nothing in it is shared with
 * anything else; splitting it would buy cross-stack references and a deployment
 * order to remember, in exchange for nothing.
 *
 * Four properties are structural rather than configured, and each is asserted in
 * `test/pilot-stack.test.ts`:
 *
 * - **Both buckets are private.** No public access, no website hosting, no bucket
 *   policy granting anonymous reads. The exhibition is served through CloudFront
 *   with Origin Access Control, which is the only identity allowed to read it.
 * - **The public API has no mutating route.** Only `GET` is registered, and the
 *   only Lambda behind it is the read handler, whose role cannot write to the table.
 * - **The schedule is created disabled.** In every environment. Arming it is an
 *   operator action against a deployed stack.
 * - **No role holds a wildcard action.** Each function's policy names the actions it
 *   makes and the resources it makes them against.
 */
import { Aws, CfnOutput, Duration, RemovalPolicy, Stack, type StackProps } from 'aws-cdk-lib';
import { HttpApi, HttpMethod, CorsHttpMethod } from 'aws-cdk-lib/aws-apigatewayv2';
import { HttpLambdaIntegration } from 'aws-cdk-lib/aws-apigatewayv2-integrations';
import * as cloudwatch from 'aws-cdk-lib/aws-cloudwatch';
import * as cloudfront from 'aws-cdk-lib/aws-cloudfront';
import * as origins from 'aws-cdk-lib/aws-cloudfront-origins';
import * as dynamodb from 'aws-cdk-lib/aws-dynamodb';
import * as events from 'aws-cdk-lib/aws-events';
import * as targets from 'aws-cdk-lib/aws-events-targets';
import * as iam from 'aws-cdk-lib/aws-iam';
import * as lambda from 'aws-cdk-lib/aws-lambda';
import * as logs from 'aws-cdk-lib/aws-logs';
import * as s3 from 'aws-cdk-lib/aws-s3';
import * as s3deploy from 'aws-cdk-lib/aws-s3-deployment';
import * as scheduler from 'aws-cdk-lib/aws-scheduler';
import * as sqs from 'aws-cdk-lib/aws-sqs';
import type { Construct } from 'constructs';

import { type EnvironmentConfig } from './environments.js';
import { WEB_DIST_DIR, lambdaBundleDir, webDistPresent } from './lambda-bundle.js';

/** The five model roles the experiment resolves, exactly as the gateway names them. */
export interface ModelIdentifiers {
  readonly writerModelId: string;
  readonly auditorModelId: string;
  readonly judgeModelId: string;
  readonly summaryModelId: string;
  readonly embeddingModelId: string;
}

export interface PilotStackProps extends StackProps {
  readonly config: EnvironmentConfig;
  /**
   * Which counter denominates the active-memory budget.
   *
   * Declared at deploy time rather than compiled in, and recorded in the template,
   * because it is an experimental parameter: a run counted one way is not comparable
   * with a run counted another. `bedrock` and `converse` are the two exact counters
   * and the only values a canonical run may use; `heuristic` is an approximation a
   * deployment may declare and a canonical run may not (ADR-011, ADR-012, ADR-013).
   */
  readonly tokenCountSource?: 'bedrock' | 'converse' | 'heuristic';
  /**
   * The git commit this bundle was built from.
   *
   * Recorded on every function so that a run's manifest names the code that produced
   * it. Optional: a deployment from a checkout with no git history is a legitimate
   * state, and recording nothing is better than recording something invented.
   */
  readonly gitCommit?: string;
  /**
   * Browser origins the read API answers, on top of the environment's own list.
   *
   * Configured rather than derived, because deriving it would mean the API
   * depending on the distribution while the distribution's content security policy
   * already depends on the API -- a cycle CloudFormation cannot deploy. A new stack
   * is therefore deployed twice: once to learn the distribution's domain, and again
   * with that domain supplied here. `make aws-deploy` does both passes.
   */
  readonly allowedOrigins?: readonly string[];
  /**
   * The Bedrock models this deployment may invoke.
   *
   * Absent is a supported state and is what CI synthesises. No identifier is
   * compiled in (ADR-006), so a stack deployed without them grants no Bedrock
   * permission and sets no model variable, and its functions refuse to start rather
   * than reaching for a default nobody recorded.
   */
  readonly models?: ModelIdentifiers;
}

/**
 * The one export prefix the distribution serves.
 *
 * `attention_sink.aws.exports.CANONICAL_PREFIX`. Only the canonical prefix is exposed:
 * a canonical export is written once and never overwritten, which is what makes it
 * safe to hand a reader a permanent URL. Non-canonical exports live under a different
 * prefix and stay private, because a staging rehearsal is not a published dataset.
 */
const EXPORT_PREFIX = 'canonical';

const RUNTIME = lambda.Runtime.PYTHON_3_12;
const ARCHITECTURE = lambda.Architecture.ARM_64;

export class PilotStack extends Stack {
  readonly table: dynamodb.Table;
  readonly exportBucket: s3.Bucket;
  readonly frontendBucket: s3.Bucket;
  readonly api: HttpApi;
  readonly distribution: cloudfront.Distribution;
  private securityHeadersPolicy?: cloudfront.ResponseHeadersPolicy;
  readonly runCycleFunction: lambda.Function;
  readonly analysisFunction: lambda.Function;
  readonly readApiFunction: lambda.Function;
  readonly schedule: scheduler.CfnSchedule;
  readonly schedulerDlq: sqs.Queue;
  readonly analysisDlq: sqs.Queue;

  constructor(scope: Construct, id: string, props: PilotStackProps) {
    super(scope, id, props);
    const { config, models } = props;
    const tokenCountSource = props.tokenCountSource ?? 'bedrock';
    const gitCommit = props.gitCommit?.trim() ?? '';

    // The exhibition and the API are on different origins, so a browser discards an
    // otherwise-successful response without a matching CORS header. Never a wildcard:
    // the API is read-only, and "read-only" is not a reason to let any page on the
    // internet read a run.
    const allowedOrigins = [...config.allowedOrigins, ...(props.allowedOrigins ?? [])];

    // ------------------------------------------------------------------ storage

    this.table = new dynamodb.Table(this, 'PilotTable', {
      partitionKey: { name: 'PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'SK', type: dynamodb.AttributeType.STRING },
      // On demand, because a pilot's traffic is one cycle an hour and six reads a
      // page view. Provisioned capacity would be a number to tune for no benefit.
      billingMode: dynamodb.BillingMode.PAY_PER_REQUEST,
      encryption: dynamodb.TableEncryption.AWS_MANAGED,
      pointInTimeRecoverySpecification: {
        pointInTimeRecoveryEnabled: config.pointInTimeRecovery,
      },
      deletionProtection: config.deletionProtection,
      removalPolicy: config.removalPolicy,
    });
    // Newest-first run listing, and one arm's snapshots in cycle order. Both are
    // read on every page view, and neither can be served by the table's own key.
    this.table.addGlobalSecondaryIndex({
      indexName: 'GSI1',
      partitionKey: { name: 'GSI1PK', type: dynamodb.AttributeType.STRING },
      sortKey: { name: 'GSI1SK', type: dynamodb.AttributeType.STRING },
      projectionType: dynamodb.ProjectionType.ALL,
    });

    this.exportBucket = new s3.Bucket(this, 'ExportBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      // Versioned because the canonical prefix is written once and a bucket that
      // could lose it to a mistaken overwrite would make that promise a convention.
      versioned: true,
      removalPolicy: config.removalPolicy,
      autoDeleteObjects: config.removalPolicy === RemovalPolicy.DESTROY,
    });

    this.frontendBucket = new s3.Bucket(this, 'FrontendBucket', {
      blockPublicAccess: s3.BlockPublicAccess.BLOCK_ALL,
      encryption: s3.BucketEncryption.S3_MANAGED,
      enforceSSL: true,
      removalPolicy: config.removalPolicy,
      autoDeleteObjects: config.removalPolicy === RemovalPolicy.DESTROY,
    });

    // ------------------------------------------------------------------- queues

    this.schedulerDlq = new sqs.Queue(this, 'SchedulerDlq', {
      retentionPeriod: Duration.days(14),
      enforceSSL: true,
    });
    this.analysisDlq = new sqs.Queue(this, 'AnalysisDlq', {
      retentionPeriod: Duration.days(14),
      enforceSSL: true,
    });

    // ---------------------------------------------------------------- functions

    const code = lambda.Code.fromAsset(lambdaBundleDir());
    const shared = {
      AS_DEPLOYMENT_ENVIRONMENT: config.name,
      AS_TABLE_NAME: this.table.tableName,
      AS_PILOT_RUN_ID: config.runId,
      AS_EXPORT_BUCKET: this.exportBucket.bucketName,
      AS_PROTOCOL_ROOT: '/var/task/experiment/pilot',
      // Never a wildcard. The read API is read-only, but "read-only" is not a reason
      // to let any page on the internet read a run.
      AS_ALLOWED_ORIGINS: allowedOrigins.join(','),
      AS_RUNTIME_MODE: config.name === 'local' ? 'local' : 'production',
      // The commit the bundle was built from, so a run records the code that
      // produced it and not only the version number that code claims. Empty when
      // the deployment could not resolve one, which reads as null in a manifest
      // rather than as a commit nobody can look up.
      ...(gitCommit ? { AS_GIT_COMMIT: gitCommit } : {}),
      // Both off. The stack deploys inert and an operator arms it.
      AS_EXECUTION_ENABLED: String(config.executionEnabled),
      ALLOW_BEDROCK_CALLS: String(config.allowBedrockCalls),
      MODEL_MODE: models ? 'bedrock' : 'fixture',
      TOKEN_COUNT_SOURCE: tokenCountSource,
      ...(config.maximumCycles === undefined
        ? {}
        : { AS_MAX_CYCLES: String(config.maximumCycles) }),
      // AWS_REGION is set by the Lambda runtime itself and is reserved, so the
      // gateway reads the function's own Region rather than one repeated here.
      ...(models
        ? {
            WRITER_MODEL_ID: models.writerModelId,
            AUDITOR_MODEL_ID: models.auditorModelId,
            JUDGE_MODEL_ID: models.judgeModelId,
            SUMMARY_MODEL_ID: models.summaryModelId,
            EMBEDDING_MODEL_ID: models.embeddingModelId,
          }
        : {}),
    };

    this.runCycleFunction = this.pythonFunction('RunCycleFunction', {
      handler: 'attention_sink.aws.run_cycle.handler',
      code,
      config,
      environment: shared,
      // One cycle is six writer calls and up to two summaries, run with bounded
      // concurrency. Ten minutes is generous for that and well under the lock's
      // five-minute lease renewal window being needed at all.
      timeout: Duration.minutes(10),
      memorySize: 1024,
    });

    this.analysisFunction = this.pythonFunction('AnalysisFunction', {
      handler: 'attention_sink.aws.analysis.handler',
      code,
      config,
      environment: shared,
      // A whole-run pass, growing with the number of committed cycles.
      timeout: Duration.minutes(15),
      memorySize: 1536,
      deadLetterQueue: this.analysisDlq,
    });

    this.readApiFunction = this.pythonFunction('ReadApiFunction', {
      handler: 'attention_sink.aws.read_api.handler',
      code,
      config,
      environment: {
        ...shared,
        // The read API never executes anything and never calls a model, whatever
        // the rest of the deployment is armed for.
        AS_EXECUTION_ENABLED: 'false',
        ALLOW_BEDROCK_CALLS: 'false',
      },
      timeout: Duration.seconds(29),
      memorySize: 512,
    });

    this.grantLeastPrivilege(models);

    // --------------------------------------------------------------- the API

    this.api = new HttpApi(this, 'ReadApi', {
      description: 'Attention Sink read API. Committed data only, GET only.',
      corsPreflight: {
        // Never a wildcard, and never a method that could change anything.
        allowOrigins: allowedOrigins,
        allowMethods: [CorsHttpMethod.GET],
        allowHeaders: ['content-type', 'accept'],
        maxAge: Duration.hours(1),
      },
    });
    this.api.addRoutes({
      path: '/{proxy+}',
      // GET only, declared here as well as in the application. A route table that
      // cannot express a write is one fewer thing that depends on the handler.
      methods: [HttpMethod.GET],
      integration: new HttpLambdaIntegration('ReadApiIntegration', this.readApiFunction),
    });

    // --------------------------------------------------------- the distribution

    this.distribution = new cloudfront.Distribution(this, 'Distribution', {
      comment: `Attention Sink ${config.name}`,
      defaultRootObject: 'index.html',
      defaultBehavior: {
        // `as s3.IBucket`: the jsii-generated `Bucket` declares `isWebsite` as
        // `boolean | undefined`, which `exactOptionalPropertyTypes` will not accept
        // for the interface's optional `boolean`. The cast is between two types the
        // library itself considers the same one.
        origin: origins.S3BucketOrigin.withOriginAccessControl(this.frontendBucket as s3.IBucket),
        viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
        allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
        compress: true,
        responseHeadersPolicy: this.securityHeaders(),
      },
      additionalBehaviors: {
        // The dataset, read-only, through the same Origin Access Control as the
        // exhibition. The export bucket stays private -- nothing is made public here;
        // CloudFront is given the one prefix an export is written under, and a reader
        // who wants the evidence can fetch it without an AWS account. Without this the
        // exhibition can describe a dataset it gives nobody any way to download.
        [`/${EXPORT_PREFIX}/*`]: {
          origin: origins.S3BucketOrigin.withOriginAccessControl(this.exportBucket as s3.IBucket),
          viewerProtocolPolicy: cloudfront.ViewerProtocolPolicy.REDIRECT_TO_HTTPS,
          allowedMethods: cloudfront.AllowedMethods.ALLOW_GET_HEAD,
          compress: true,
          responseHeadersPolicy: this.securityHeaders(),
        },
      },
      errorResponses: [
        // A single-page application: every path that is not a file is a route the
        // client resolves. Without this, a reload of /graveyard is a 403 from S3.
        { httpStatus: 403, responseHttpStatus: 200, responsePagePath: '/index.html' },
        { httpStatus: 404, responseHttpStatus: 200, responsePagePath: '/index.html' },
      ],
      priceClass: cloudfront.PriceClass.PRICE_CLASS_100,
      // No `minimumProtocolVersion`: it has no effect without a custom certificate,
      // and a setting that reads like a guarantee while doing nothing is worse than
      // its absence. HTTPS is enforced by the viewer protocol policy above.
    });

    if (webDistPresent()) {
      // Absent on a new stack's first deployment: the exhibition is compiled
      // against the API's URL, and that URL does not exist yet. `make aws-deploy`
      // deploys, reads the URL, builds, and deploys again.
      new s3deploy.BucketDeployment(this, 'FrontendDeployment', {
        sources: [s3deploy.Source.asset(WEB_DIST_DIR)],
        // The same jsii/`exactOptionalPropertyTypes` mismatch as the origin above.
        destinationBucket: this.frontendBucket as s3.IBucket,
        distribution: this.distribution,
        distributionPaths: ['/*'],
        prune: true,
      });
    }

    // ------------------------------------------------------------- the schedule

    this.schedule = this.cycleSchedule(config);

    // ----------------------------------------------------------------- the rule

    const rule = new events.Rule(this, 'CycleCompletedRule', {
      description: 'A committed cycle, routed to analysis.',
      eventPattern: {
        source: ['attention-sink.pilot'],
        detailType: ['CycleCompleted'],
      },
    });
    rule.addTarget(
      new targets.LambdaFunction(this.analysisFunction, {
        deadLetterQueue: this.analysisDlq,
        retryAttempts: 2,
        maxEventAge: Duration.hours(1),
      }),
    );
    // The default bus by name, not `grantAllPutEvents`, which grants
    // `events:PutEvents` on every bus in the account.
    this.runCycleFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['events:PutEvents'],
        resources: [`arn:${Aws.PARTITION}:events:${this.region}:${this.account}:event-bus/default`],
      }),
    );

    this.alarms(config);
    this.outputs(config, tokenCountSource);
  }

  // ------------------------------------------------------------------ helpers

  private pythonFunction(
    id: string,
    options: {
      handler: string;
      code: lambda.Code;
      config: EnvironmentConfig;
      environment: Record<string, string>;
      timeout: Duration;
      memorySize: number;
      deadLetterQueue?: sqs.Queue;
    },
  ): lambda.Function {
    const logGroup = new logs.LogGroup(this, `${id}Logs`, {
      retention: options.config.logRetention,
      removalPolicy: options.config.removalPolicy,
    });
    return new lambda.Function(this, id, {
      runtime: RUNTIME,
      architecture: ARCHITECTURE,
      handler: options.handler,
      code: options.code,
      environment: options.environment,
      timeout: options.timeout,
      memorySize: options.memorySize,
      logGroup,
      // One at a time. Two invocations of the cycle function would contend for the
      // same lock and one would waste a cold start losing the race; the read API is
      // given room because it is the only thing a visitor waits on. A single page
      // load opens several requests at once, so a handful of simultaneous visitors
      // reaches tens of concurrent invocations, and a throttled one is a 503 on a
      // public exhibition. The cap stays -- it is the runaway guard -- but it is set
      // above what a burst of readers costs, which is nothing: this function reads
      // DynamoDB and calls no model.
      reservedConcurrentExecutions: id === 'ReadApiFunction' ? 100 : 2,
      ...(options.deadLetterQueue ? { deadLetterQueue: options.deadLetterQueue } : {}),
    });
  }

  /**
   * The policies each function actually needs, written out rather than granted.
   *
   * `grantReadWriteData` would add `Scan`, `BatchWriteItem`, and `DeleteItem` to
   * every role that writes anything. Naming the actions is longer and is the point:
   * the read API's role cannot write, and no role can scan the table.
   */
  private grantLeastPrivilege(models: ModelIdentifiers | undefined): void {
    const tableResources = [this.table.tableArn, `${this.table.tableArn}/index/*`];

    const cycleWrites = new iam.PolicyStatement({
      actions: [
        'dynamodb:GetItem',
        'dynamodb:PutItem',
        'dynamodb:UpdateItem',
        'dynamodb:DeleteItem',
        'dynamodb:Query',
        'dynamodb:TransactWriteItems',
      ],
      resources: tableResources,
    });
    this.runCycleFunction.addToRolePolicy(cycleWrites);
    this.analysisFunction.addToRolePolicy(cycleWrites);

    this.readApiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        // No write action of any kind. The public surface cannot change the
        // experiment even if the application one day tried to.
        actions: ['dynamodb:GetItem', 'dynamodb:Query'],
        resources: tableResources,
      }),
    );
    this.readApiFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:GetObject'],
        resources: [`${this.exportBucket.bucketArn}/*`],
      }),
    );
    this.analysisFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['s3:PutObject', 's3:GetObject', 's3:ListBucket'],
        resources: [this.exportBucket.bucketArn, `${this.exportBucket.bucketArn}/*`],
      }),
    );

    if (!models) {
      return;
    }
    const arns = (...ids: string[]): string[] =>
      ids.flatMap((id) => [
        `arn:${Aws.PARTITION}:bedrock:${this.region}::foundation-model/${id}`,
        // Cross-Region inference profiles are account-scoped resources of their own,
        // and a model reached through one is denied without this second ARN.
        `arn:${Aws.PARTITION}:bedrock:${this.region}:${this.account}:inference-profile/${id}`,
      ]);

    this.runCycleFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:CountTokens'],
        resources: arns(models.writerModelId, models.summaryModelId, models.auditorModelId),
      }),
    );
    this.analysisFunction.addToRolePolicy(
      new iam.PolicyStatement({
        actions: ['bedrock:InvokeModel', 'bedrock:CountTokens'],
        // The interviewer is the writer's model: the same agent answering questions.
        resources: arns(models.judgeModelId, models.embeddingModelId, models.writerModelId),
      }),
    );
  }

  /** A schedule that exists, is disabled, and names exactly one run. */
  private cycleSchedule(config: EnvironmentConfig): scheduler.CfnSchedule {
    const role = new iam.Role(this, 'SchedulerRole', {
      assumedBy: new iam.ServicePrincipal('scheduler.amazonaws.com'),
      description: 'Invokes the run-cycle function, and nothing else.',
    });
    role.addToPolicy(
      new iam.PolicyStatement({
        actions: ['lambda:InvokeFunction'],
        resources: [this.runCycleFunction.functionArn],
      }),
    );
    this.schedulerDlq.grantSendMessages(role);

    return new scheduler.CfnSchedule(this, 'CycleSchedule', {
      // Named rather than generated, so the operator commands can address it
      // without first looking up a stack output.
      name: `attention-sink-${config.name}-cycle`,
      description: 'One cycle per tick. Disabled until an operator arms it.',
      flexibleTimeWindow: { mode: 'OFF' },
      scheduleExpression: config.scheduleExpression,
      scheduleExpressionTimezone: 'UTC',
      // Disabled in every environment. The one switch that would spend money on a
      // timer is the one switch that is never on by default.
      state: config.schedulerEnabled ? 'ENABLED' : 'DISABLED',
      target: {
        arn: this.runCycleFunction.functionArn,
        roleArn: role.roleArn,
        // The run is fixed in the payload, not read from the environment at fire
        // time, so a schedule cannot start advancing a different run than the one
        // it was created for.
        input: JSON.stringify({ run_id: config.runId, source: 'scheduler' }),
        retryPolicy: { maximumRetryAttempts: 2, maximumEventAgeInSeconds: 3600 },
        deadLetterConfig: { arn: this.schedulerDlq.queueArn },
      },
    });
  }

  /**
   * Security headers for everything the distribution serves.
   *
   * The content security policy is restrictive because the exhibition needs almost
   * nothing: its own scripts and styles, its own fonts, and one API. `connect-src`
   * names the API's domain rather than allowing any origin, so a compromised script
   * could not exfiltrate to somewhere else.
   *
   * Built once and reused. Every behaviour on the distribution wants the same headers,
   * and a second call would try to create a second construct under the same id.
   */
  private securityHeaders(): cloudfront.ResponseHeadersPolicy {
    if (this.securityHeadersPolicy) return this.securityHeadersPolicy;
    const api = `https://${this.api.apiId}.execute-api.${this.region}.${Aws.URL_SUFFIX}`;
    const policy = [
      "default-src 'none'",
      "script-src 'self'",
      // Vite emits one inline style element for the bundled stylesheet.
      "style-src 'self' 'unsafe-inline'",
      "img-src 'self' data:",
      "font-src 'self'",
      `connect-src 'self' ${api}`,
      "base-uri 'none'",
      "form-action 'none'",
      "frame-ancestors 'none'",
      "object-src 'none'",
      'upgrade-insecure-requests',
    ].join('; ');

    this.securityHeadersPolicy = new cloudfront.ResponseHeadersPolicy(this, 'SecurityHeaders', {
      securityHeadersBehavior: {
        contentSecurityPolicy: { contentSecurityPolicy: policy, override: true },
        contentTypeOptions: { override: true },
        frameOptions: {
          frameOption: cloudfront.HeadersFrameOption.DENY,
          override: true,
        },
        referrerPolicy: {
          referrerPolicy: cloudfront.HeadersReferrerPolicy.NO_REFERRER,
          override: true,
        },
        strictTransportSecurity: {
          accessControlMaxAge: Duration.days(365),
          includeSubdomains: true,
          preload: true,
          override: true,
        },
        xssProtection: { protection: true, modeBlock: true, override: true },
      },
    });
    return this.securityHeadersPolicy;
  }

  /**
   * The seven conditions worth waking somebody for.
   *
   * Three are Lambda's own metrics. Four are metric filters over the structured log
   * groups, because "the run stopped advancing" and "a cycle used four times the
   * tokens it should have" are facts about the experiment and there is no service
   * metric for either.
   */
  private alarms(config: EnvironmentConfig): void {
    const namespace = `AttentionSink/${config.name}`;

    const committed = new logs.MetricFilter(this, 'CommittedCycles', {
      logGroup: this.runCycleFunction.logGroup,
      metricNamespace: namespace,
      metricName: 'CommittedCycles',
      filterPattern: logs.FilterPattern.stringValue('$.result_code', '=', 'committed'),
      metricValue: '1',
      defaultValue: 0,
    });
    const inputTokens = new logs.MetricFilter(this, 'CycleInputTokens', {
      logGroup: this.runCycleFunction.logGroup,
      metricNamespace: namespace,
      metricName: 'CycleInputTokens',
      filterPattern: logs.FilterPattern.stringValue('$.result_code', '=', 'committed'),
      metricValue: '$.input_tokens',
    });
    const limitReached = new logs.MetricFilter(this, 'ModelCallLimitReached', {
      logGroup: this.runCycleFunction.logGroup,
      metricNamespace: namespace,
      metricName: 'ModelCallLimitReached',
      filterPattern: logs.FilterPattern.stringValue('$.result_code', '=', 'model_call_limit'),
      metricValue: '1',
      defaultValue: 0,
    });
    const failedCycle = new logs.MetricFilter(this, 'FailedCycles', {
      logGroup: this.runCycleFunction.logGroup,
      metricNamespace: namespace,
      metricName: 'FailedCycles',
      filterPattern: logs.FilterPattern.stringValue('$.result_code', '=', 'cycle_failed'),
      metricValue: '1',
      defaultValue: 0,
    });

    new cloudwatch.Alarm(this, 'FailedScheduledCycleAlarm', {
      alarmDescription: 'A scheduled cycle failed to commit.',
      metric: failedCycle.metric({ statistic: 'Sum', period: Duration.minutes(15) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'ModelCallLimitAlarm', {
      alarmDescription: 'A cycle hit the protocol model-call ceiling and stopped.',
      metric: limitReached.metric({ statistic: 'Sum', period: Duration.hours(1) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'CycleNotAdvancingAlarm', {
      alarmDescription:
        'No cycle committed in six hours while the schedule was expected to fire. ' +
        'Missing data breaches on purpose: silence is the symptom.',
      metric: committed.metric({ statistic: 'Sum', period: Duration.hours(6) }),
      threshold: 1,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
      treatMissingData: config.schedulerEnabled
        ? cloudwatch.TreatMissingData.BREACHING
        : cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    new cloudwatch.Alarm(this, 'AbnormalTokenUseAlarm', {
      alarmDescription:
        'A cycle consumed far more input tokens than the budget implies, which ' +
        'means a prompt is carrying something it should not.',
      metric: inputTokens.metric({ statistic: 'Maximum', period: Duration.hours(1) }),
      // Roughly four times a real cycle. The canonical run's cycles cost twelve to
      // fifteen thousand input tokens each: six writer requests around a memory block
      // that is capped by the budget, plus six counting calls. Anything at four times
      // that is a prompt carrying something the protocol did not put in it.
      threshold: 50_000,
      evaluationPeriods: 1,
      comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_THRESHOLD,
      treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
    });

    for (const [id, queue] of [
      ['SchedulerDlqAlarm', this.schedulerDlq],
      ['AnalysisDlqAlarm', this.analysisDlq],
    ] as const) {
      new cloudwatch.Alarm(this, id, {
        alarmDescription: `A message reached ${queue.node.id}; something failed every retry.`,
        metric: queue.metricApproximateNumberOfMessagesVisible({
          statistic: 'Maximum',
          period: Duration.minutes(5),
        }),
        threshold: 1,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }

    for (const [id, fn] of [
      ['RunCycleErrorsAlarm', this.runCycleFunction],
      ['AnalysisErrorsAlarm', this.analysisFunction],
      ['ReadApiErrorsAlarm', this.readApiFunction],
    ] as const) {
      new cloudwatch.Alarm(this, id, {
        alarmDescription: `${fn.node.id} failed repeatedly.`,
        metric: fn.metricErrors({ statistic: 'Sum', period: Duration.minutes(15) }),
        threshold: 3,
        evaluationPeriods: 1,
        comparisonOperator: cloudwatch.ComparisonOperator.GREATER_THAN_OR_EQUAL_TO_THRESHOLD,
        treatMissingData: cloudwatch.TreatMissingData.NOT_BREACHING,
      });
    }
  }

  /** Everything an operator needs to drive the deployment, and no secret. */
  private outputs(config: EnvironmentConfig, tokenCountSource: string): void {
    const values: Record<string, string> = {
      DeploymentEnvironment: config.name,
      Region: this.region,
      TableName: this.table.tableName,
      ApiUrl: this.api.apiEndpoint,
      CloudFrontUrl: `https://${this.distribution.distributionDomainName}`,
      FrontendBucketName: this.frontendBucket.bucketName,
      ExportBucketName: this.exportBucket.bucketName,
      RunCycleFunctionName: this.runCycleFunction.functionName,
      AnalysisFunctionName: this.analysisFunction.functionName,
      ReadApiFunctionName: this.readApiFunction.functionName,
      ScheduleName: this.schedule.name ?? '',
      SchedulerDlqUrl: this.schedulerDlq.queueUrl,
      AnalysisDlqUrl: this.analysisDlq.queueUrl,
      PilotRunId: config.runId,
      TokenCountSource: tokenCountSource,
    };
    for (const [name, value] of Object.entries(values)) {
      new CfnOutput(this, name, { value, exportName: `${this.stackName}-${name}` });
    }
  }
}
