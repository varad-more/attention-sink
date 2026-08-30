/**
 * What the deployed stack must be true of, asserted against the template.
 *
 * These run before every deployment. They are not a re-statement of the stack file:
 * each one is a property that would be expensive or embarrassing to discover in an
 * account -- a public bucket, a write route on a public API, a schedule that started
 * spending the moment it was created, a role with a wildcard action.
 */
import { App } from 'aws-cdk-lib';
import { Match, Template } from 'aws-cdk-lib/assertions';
import { beforeAll, describe, expect, it } from 'vitest';

import { ENVIRONMENTS, environmentConfig } from '../lib/environments.js';
import { PilotStack, type ModelIdentifiers } from '../lib/pilot-stack.js';

const MODELS: ModelIdentifiers = {
  writerModelId: 'amazon.nova-2-lite-v1:0',
  auditorModelId: 'amazon.nova-2-lite-v1:0',
  judgeModelId: 'amazon.nova-2-lite-v1:0',
  summaryModelId: 'amazon.nova-2-lite-v1:0',
  embeddingModelId: 'amazon.titan-embed-text-v2:0',
};

/**
 * Synthesise one environment.
 *
 * `models` takes `null` rather than `undefined` for "no models configured", because
 * an explicitly-passed `undefined` selects a default parameter in JavaScript -- which
 * would have made the no-Bedrock assertion below silently test the configured case.
 */
function synthesise(
  environment: 'local' | 'staging' | 'production' = 'staging',
  models: ModelIdentifiers | null = MODELS,
): Template {
  const stack = new PilotStack(new App(), `Test-${environment}`, {
    env: { account: '000000000000', region: 'us-east-1' },
    config: environmentConfig(environment),
    ...(models ? { models } : {}),
  });
  return Template.fromStack(stack);
}

/**
 * This project's own functions, without the ones CDK adds for its custom resources.
 *
 * `autoDeleteObjects` and `BucketDeployment` each contribute a handler of their own.
 * Counting them would make every assertion about "the functions" depend on how many
 * helpers the toolkit happens to ship.
 */
function ourFunctions(template: Template): Record<string, Record<string, any>> {
  return Object.fromEntries(
    Object.entries(template.findResources('AWS::Lambda::Function')).filter(([, fn]) =>
      String(fn.Properties.Handler).startsWith('attention_sink.'),
    ),
  );
}

let staging: Template;

beforeAll(() => {
  staging = synthesise('staging');
});

describe('synthesis', () => {
  it('synthesises every environment', () => {
    for (const name of Object.keys(ENVIRONMENTS)) {
      expect(() => synthesise(name as 'staging')).not.toThrow();
    }
  });

  it('refuses an environment name it does not know', () => {
    // A typo must not resolve to the most permissive configuration, or to any.
    expect(() => environmentConfig('prod')).toThrow(/unknown deployment environment/);
  });

  it('declares one table, three functions, one API, and two buckets', () => {
    staging.resourceCountIs('AWS::DynamoDB::Table', 1);
    expect(Object.keys(ourFunctions(staging))).toHaveLength(3);
    staging.resourceCountIs('AWS::ApiGatewayV2::Api', 1);
    staging.resourceCountIs('AWS::S3::Bucket', 2);
    staging.resourceCountIs('AWS::CloudFront::Distribution', 1);
    staging.resourceCountIs('AWS::Scheduler::Schedule', 1);
    staging.resourceCountIs('AWS::SQS::Queue', 2);
  });
});

describe('the table', () => {
  it('carries the index the read paths depend on', () => {
    staging.hasResourceProperties(
      'AWS::DynamoDB::Table',
      Match.objectLike({
        KeySchema: [
          { AttributeName: 'PK', KeyType: 'HASH' },
          { AttributeName: 'SK', KeyType: 'RANGE' },
        ],
        GlobalSecondaryIndexes: Match.arrayWith([
          Match.objectLike({
            IndexName: 'GSI1',
            KeySchema: [
              { AttributeName: 'GSI1PK', KeyType: 'HASH' },
              { AttributeName: 'GSI1SK', KeyType: 'RANGE' },
            ],
          }),
        ]),
      }),
    );
  });

  it('is encrypted and recoverable in staging and production', () => {
    for (const environment of ['staging', 'production'] as const) {
      synthesise(environment).hasResourceProperties(
        'AWS::DynamoDB::Table',
        Match.objectLike({
          PointInTimeRecoverySpecification: { PointInTimeRecoveryEnabled: true },
          SSESpecification: Match.objectLike({ SSEEnabled: true }),
        }),
      );
    }
  });

  it('is deletion-protected and retained in production only', () => {
    synthesise('production').hasResource(
      'AWS::DynamoDB::Table',
      Match.objectLike({
        DeletionPolicy: 'Retain',
        Properties: Match.objectLike({ DeletionProtectionEnabled: true }),
      }),
    );
    synthesise('staging').hasResource(
      'AWS::DynamoDB::Table',
      Match.objectLike({
        DeletionPolicy: 'Delete',
        Properties: Match.objectLike({ DeletionProtectionEnabled: false }),
      }),
    );
  });
});

describe('the buckets', () => {
  it('block all public access', () => {
    const buckets = staging.findResources('AWS::S3::Bucket');
    expect(Object.keys(buckets)).toHaveLength(2);
    for (const bucket of Object.values(buckets)) {
      expect(bucket.Properties.PublicAccessBlockConfiguration).toEqual({
        BlockPublicAcls: true,
        BlockPublicPolicy: true,
        IgnorePublicAcls: true,
        RestrictPublicBuckets: true,
      });
      expect(bucket.Properties.BucketEncryption).toBeDefined();
      // Nothing is served as a website. A website endpoint is public by
      // construction and would route around CloudFront entirely.
      expect(bucket.Properties.WebsiteConfiguration).toBeUndefined();
    }
  });

  it('allow no anonymous principal', () => {
    // Allow statements only. The `enforceSSL` rule is a Deny against every
    // principal, and reading it as a grant would be reading a lock as a door.
    const policies = staging.findResources('AWS::S3::BucketPolicy');
    for (const policy of Object.values(policies)) {
      const allows = policy.Properties.PolicyDocument.Statement.filter(
        (statement: { Effect: string }) => statement.Effect === 'Allow',
      );
      for (const statement of allows) {
        expect(JSON.stringify(statement.Principal ?? {})).not.toContain('"*"');
      }
    }
  });

  it('versions the export bucket, so a canonical dataset cannot be lost', () => {
    staging.hasResourceProperties(
      'AWS::S3::Bucket',
      Match.objectLike({ VersioningConfiguration: { Status: 'Enabled' } }),
    );
  });
});

describe('the distribution', () => {
  it('reads S3 through Origin Access Control and nothing else', () => {
    staging.resourceCountIs('AWS::CloudFront::OriginAccessControl', 1);
    staging.hasResourceProperties(
      'AWS::CloudFront::Distribution',
      Match.objectLike({
        DistributionConfig: Match.objectLike({
          Origins: Match.arrayWith([Match.objectLike({ OriginAccessControlId: Match.anyValue() })]),
        }),
      }),
    );
  });

  it('falls back to index.html so a deep link survives a reload', () => {
    staging.hasResourceProperties(
      'AWS::CloudFront::Distribution',
      Match.objectLike({
        DistributionConfig: Match.objectLike({
          CustomErrorResponses: Match.arrayWith([
            Match.objectLike({
              ErrorCode: 403,
              ResponseCode: 200,
              ResponsePagePath: '/index.html',
            }),
          ]),
        }),
      }),
    );
  });

  it('compresses, redirects to HTTPS, and sends security headers', () => {
    staging.hasResourceProperties(
      'AWS::CloudFront::Distribution',
      Match.objectLike({
        DistributionConfig: Match.objectLike({
          DefaultCacheBehavior: Match.objectLike({
            Compress: true,
            ViewerProtocolPolicy: 'redirect-to-https',
            ResponseHeadersPolicyId: Match.anyValue(),
          }),
        }),
      }),
    );
  });

  it('sets a content security policy that names one API and no wildcard host', () => {
    const policies = staging.findResources('AWS::CloudFront::ResponseHeadersPolicy');
    const [policy] = Object.values(policies);
    expect(policy).toBeDefined();
    const csp = JSON.stringify(
      policy?.Properties.ResponseHeadersPolicyConfig.SecurityHeadersConfig.ContentSecurityPolicy,
    );
    expect(csp).toContain("default-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    expect(csp).toContain('execute-api');
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain('connect-src *');
  });
});

describe('the public API', () => {
  it('registers GET and nothing else', () => {
    const routes = staging.findResources('AWS::ApiGatewayV2::Route');
    const keys = Object.values(routes).map((route) => route.Properties.RouteKey as string);
    expect(keys.length).toBeGreaterThan(0);
    for (const key of keys) {
      expect(key.startsWith('GET ')).toBe(true);
    }
  });

  it('has no POST, PUT, PATCH, or DELETE route anywhere in the template', () => {
    // The check that matters most, phrased so it fails on any future addition.
    const routes = staging.findResources('AWS::ApiGatewayV2::Route');
    const forbidden = ['POST', 'PUT', 'PATCH', 'DELETE', 'ANY'];
    for (const route of Object.values(routes)) {
      for (const verb of forbidden) {
        expect(route.Properties.RouteKey).not.toContain(`${verb} `);
      }
    }
  });

  it('never allows a wildcard CORS origin', () => {
    const apis = staging.findResources('AWS::ApiGatewayV2::Api');
    for (const api of Object.values(apis)) {
      const cors = api.Properties.CorsConfiguration;
      expect(cors?.AllowOrigins ?? []).not.toContain('*');
      expect(cors?.AllowMethods ?? ['GET']).toEqual(['GET']);
    }
  });
});

describe('the schedule', () => {
  it('is created disabled in every environment', () => {
    for (const name of Object.keys(ENVIRONMENTS)) {
      synthesise(name as 'staging').hasResourceProperties(
        'AWS::Scheduler::Schedule',
        Match.objectLike({ State: 'DISABLED' }),
      );
    }
  });

  it('names one run in its payload and has a dead-letter queue and retries', () => {
    staging.hasResourceProperties(
      'AWS::Scheduler::Schedule',
      Match.objectLike({
        Target: Match.objectLike({
          Input: Match.stringLikeRegexp('run_aws_staging'),
          DeadLetterConfig: { Arn: Match.anyValue() },
          RetryPolicy: { MaximumRetryAttempts: 2, MaximumEventAgeInSeconds: 3600 },
        }),
      }),
    );
  });

  it('gives the scheduler a role that can invoke one function and nothing else', () => {
    const policies = staging.findResources('AWS::IAM::Policy');
    const schedulerPolicies = Object.values(policies).filter((policy) =>
      JSON.stringify(policy.Properties.PolicyDocument).includes('lambda:InvokeFunction'),
    );
    expect(schedulerPolicies.length).toBe(1);
  });
});

describe('the rule', () => {
  it('routes only cycle-completed events, with a dead-letter queue', () => {
    staging.hasResourceProperties(
      'AWS::Events::Rule',
      Match.objectLike({
        EventPattern: {
          source: ['attention-sink.pilot'],
          'detail-type': ['CycleCompleted'],
        },
        Targets: Match.arrayWith([
          Match.objectLike({ DeadLetterConfig: { Arn: Match.anyValue() } }),
        ]),
      }),
    );
  });
});

describe('IAM', () => {
  function statements(template: Template): { Action: unknown; Resource: unknown }[] {
    const policies = template.findResources('AWS::IAM::Policy');
    return Object.values(policies).flatMap(
      (policy) =>
        policy.Properties.PolicyDocument.Statement as { Action: unknown; Resource: unknown }[],
    );
  }

  it('grants no bare wildcard action', () => {
    for (const statement of statements(staging)) {
      const actions = Array.isArray(statement.Action) ? statement.Action : [statement.Action];
      for (const action of actions) {
        expect(action).not.toBe('*');
        expect(String(action)).not.toMatch(/^(dynamodb|s3|bedrock|events):\*$/);
      }
    }
  });

  it('never attaches an AWS managed administrator policy', () => {
    const roles = staging.findResources('AWS::IAM::Role');
    for (const role of Object.values(roles)) {
      expect(JSON.stringify(role.Properties.ManagedPolicyArns ?? [])).not.toContain(
        'AdministratorAccess',
      );
      expect(JSON.stringify(role.Properties.ManagedPolicyArns ?? [])).not.toContain('PowerUser');
    }
  });

  it('lets nothing scan the table', () => {
    // A Scan on a public path is a cost that grows with data nobody asked for, and
    // the adapter never issues one. Removing the permission makes that binding.
    expect(JSON.stringify(statements(staging))).not.toContain('dynamodb:Scan');
  });

  it('gives the read API no write action of any kind', () => {
    const policies = staging.findResources('AWS::IAM::Policy');
    const readOnly = Object.entries(policies).find(([id]) => id.startsWith('ReadApiFunction'));
    expect(readOnly).toBeDefined();
    const document = JSON.stringify(readOnly?.[1].Properties.PolicyDocument);
    for (const action of ['PutItem', 'UpdateItem', 'DeleteItem', 'TransactWriteItems']) {
      expect(document).not.toContain(action);
    }
    expect(document).toContain('dynamodb:Query');
  });

  it('scopes Bedrock to the configured models only', () => {
    const document = JSON.stringify(statements(staging));
    expect(document).toContain('bedrock:InvokeModel');
    expect(document).toContain('amazon.nova-2-lite-v1:0');
    expect(document).not.toContain('foundation-model/*');
  });

  it('grants no Bedrock permission at all when no model is configured', () => {
    // ADR-006: no identifier is compiled in. A stack deployed without them refuses
    // to start rather than reaching for a default nobody recorded.
    const unconfigured = synthesise('staging', null);
    expect(JSON.stringify(statements(unconfigured))).not.toContain('bedrock:');
  });
});

describe('the functions', () => {
  it('run the handlers the deployment package actually contains', () => {
    const handlers = Object.values(ourFunctions(staging)).map(
      (fn) => fn.Properties.Handler as string,
    );
    expect(new Set(handlers)).toEqual(
      new Set([
        'attention_sink.aws.run_cycle.handler',
        'attention_sink.aws.analysis.handler',
        'attention_sink.aws.read_api.handler',
      ]),
    );
  });

  it('deploy inert: execution and the scheduler are both off', () => {
    for (const fn of Object.values(ourFunctions(staging))) {
      expect(fn.Properties.Environment.Variables.AS_EXECUTION_ENABLED).toBe('false');
    }
  });

  it('never carries a credential in an environment variable', () => {
    for (const fn of Object.values(ourFunctions(staging))) {
      const variables = Object.keys(fn.Properties.Environment?.Variables ?? {});
      for (const name of variables) {
        expect(name).not.toMatch(/SECRET|PASSWORD|ACCESS_KEY|SESSION_TOKEN/i);
      }
    }
  });

  it('tells every function which run and which table it serves', () => {
    for (const fn of Object.values(ourFunctions(staging))) {
      const variables = fn.Properties.Environment.Variables;
      expect(variables.AS_PILOT_RUN_ID).toBe('run_aws_staging');
      expect(variables.AS_TABLE_NAME).toBeDefined();
      expect(variables.AS_DEPLOYMENT_ENVIRONMENT).toBe('staging');
      expect(variables.MODEL_MODE).toBe('bedrock');
    }
  });

  it('runs a staging deployment in production runtime mode, never on fixtures', () => {
    for (const fn of Object.values(ourFunctions(synthesise('staging')))) {
      expect(fn.Properties.Environment.Variables.AS_RUNTIME_MODE).toBe('production');
    }
  });

  it('caps staging below the protocol maximum', () => {
    for (const fn of Object.values(ourFunctions(staging))) {
      expect(fn.Properties.Environment.Variables.AS_MAX_CYCLES).toBe('3');
    }
  });

  it('gives every function its own retained log group', () => {
    staging.resourceCountIs('AWS::Logs::LogGroup', 3);
    staging.hasResourceProperties('AWS::Logs::LogGroup', Match.objectLike({ RetentionInDays: 30 }));
  });
});

describe('observability', () => {
  it('alarms on every condition worth waking somebody for', () => {
    const alarms = Object.values(staging.findResources('AWS::CloudWatch::Alarm')).map(
      (alarm) => alarm.Properties.AlarmDescription as string,
    );
    expect(alarms).toHaveLength(9);
    for (const fragment of [
      'failed to commit',
      'model-call ceiling',
      'No cycle committed',
      'more input tokens',
      'SchedulerDlq',
      'AnalysisDlq',
    ]) {
      expect(alarms.some((description) => description.includes(fragment))).toBe(true);
    }
  });

  it('derives the experiment metrics from the structured logs', () => {
    staging.resourceCountIs('AWS::Logs::MetricFilter', 4);
    staging.hasResourceProperties(
      'AWS::Logs::MetricFilter',
      Match.objectLike({
        MetricTransformations: Match.arrayWith([
          Match.objectLike({ MetricName: 'CommittedCycles' }),
        ]),
      }),
    );
  });
});

describe('outputs', () => {
  it('publishes everything an operator drives the deployment with', () => {
    const outputs = staging.toJSON().Outputs as Record<string, unknown>;
    for (const name of [
      'ApiUrl',
      'CloudFrontUrl',
      'TableName',
      'ExportBucketName',
      'FrontendBucketName',
      'RunCycleFunctionName',
      'AnalysisFunctionName',
      'ReadApiFunctionName',
      'ScheduleName',
      'SchedulerDlqUrl',
      'AnalysisDlqUrl',
      'Region',
      'PilotRunId',
    ]) {
      expect(outputs).toHaveProperty(name);
    }
  });
});

describe('the token counter', () => {
  function withCounter(source?: 'bedrock' | 'converse' | 'heuristic'): Template {
    const stack = new PilotStack(new App(), `Test-counter-${source ?? 'default'}`, {
      env: { account: '000000000000', region: 'us-east-1' },
      config: environmentConfig('staging'),
      models: MODELS,
      ...(source ? { tokenCountSource: source } : {}),
    });
    return Template.fromStack(stack);
  }

  it('deploys the exact counter unless a deployment declares otherwise', () => {
    for (const fn of Object.values(ourFunctions(withCounter()))) {
      expect(fn.Properties.Environment.Variables.TOKEN_COUNT_SOURCE).toBe('bedrock');
    }
  });

  it('carries a declared approximate counter into every function and into the outputs', () => {
    // ADR-012: declared before the run, recorded where a reader will find it, and
    // refused for a canonical run by the configuration's own validator.
    const template = withCounter('heuristic');
    for (const fn of Object.values(ourFunctions(template))) {
      expect(fn.Properties.Environment.Variables.TOKEN_COUNT_SOURCE).toBe('heuristic');
    }
    const outputs = template.toJSON().Outputs as Record<string, { Value: string }>;
    expect(outputs.TokenCountSource?.Value).toBe('heuristic');
  });

  it('carries the counter a canonical run uses, which is neither of the other two', () => {
    // ADR-013: CountTokens supports no model this account can reach, so the exact
    // count comes from an invocation capped at one output token instead.
    const template = withCounter('converse');
    for (const fn of Object.values(ourFunctions(template))) {
      expect(fn.Properties.Environment.Variables.TOKEN_COUNT_SOURCE).toBe('converse');
    }
    const outputs = template.toJSON().Outputs as Record<string, { Value: string }>;
    expect(outputs.TokenCountSource?.Value).toBe('converse');
  });
});

describe('the commit a run records', () => {
  function withCommit(gitCommit?: string): Template {
    const stack = new PilotStack(new App(), `Test-commit-${gitCommit ?? 'none'}`, {
      env: { account: '000000000000', region: 'us-east-1' },
      config: environmentConfig('production'),
      models: MODELS,
      ...(gitCommit ? { gitCommit } : {}),
    });
    return Template.fromStack(stack);
  }

  it('reaches every function, so a run names the code that produced it', () => {
    const commit = 'd5e740530600089500abc903a7c6211aac42f904';
    for (const fn of Object.values(ourFunctions(withCommit(commit)))) {
      expect(fn.Properties.Environment.Variables.AS_GIT_COMMIT).toBe(commit);
    }
  });

  it('is absent rather than invented when the deployment does not know one', () => {
    for (const fn of Object.values(ourFunctions(withCommit()))) {
      expect(fn.Properties.Environment.Variables).not.toHaveProperty('AS_GIT_COMMIT');
    }
  });
});

describe('the production deployment', () => {
  it('keeps its data when the stack goes away', () => {
    const template = synthesise('production');
    template.hasResource('AWS::DynamoDB::Table', {
      DeletionPolicy: 'Retain',
      UpdateReplacePolicy: 'Retain',
      Properties: { DeletionProtectionEnabled: true },
    });
  });

  it('imposes no cycle ceiling of its own, leaving the protocol to define the run', () => {
    for (const fn of Object.values(ourFunctions(synthesise('production')))) {
      expect(fn.Properties.Environment.Variables).not.toHaveProperty('AS_MAX_CYCLES');
    }
  });

  it('deploys inert: neither the function nor the schedule may act', () => {
    const template = synthesise('production');
    for (const fn of Object.values(ourFunctions(template))) {
      expect(fn.Properties.Environment.Variables.AS_EXECUTION_ENABLED).toBe('false');
    }
    template.hasResourceProperties('AWS::Scheduler::Schedule', { State: 'DISABLED' });
  });

  it('serves the canonical run identifier and no other', () => {
    const outputs = synthesise('production').toJSON().Outputs as Record<string, { Value: string }>;
    expect(outputs.PilotRunId?.Value).toBe('run_aws_canonical');
  });
});
