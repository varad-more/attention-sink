/**
 * What each environment is allowed to do, decided in one table.
 *
 * Every dangerous default is off. The scheduler is disabled in all three, execution
 * is disabled in all three, and no environment is canonical. Turning any of them on
 * is an explicit operator action against a deployed stack, never a consequence of
 * running `cdk deploy` against a different account.
 *
 * The two things that differ between staging and production are the cycle ceiling
 * and what happens to the data when the stack is destroyed. Staging keeps a short
 * ceiling so that arming its scheduler by mistake costs a handful of model calls,
 * and lets its table and buckets be deleted so a throwaway stack is throwaway.
 * Production keeps everything.
 */
import { RemovalPolicy } from 'aws-cdk-lib';
import { RetentionDays } from 'aws-cdk-lib/aws-logs';

/** Which deployment a stack instance is. */
export type EnvironmentName = 'local' | 'staging' | 'production';

export interface EnvironmentConfig {
  readonly name: EnvironmentName;
  /**
   * Whether the run-cycle Lambda may advance a run at all.
   *
   * False everywhere. A deployed stack that does nothing until an operator arms it
   * is the only safe default for a system whose unit of work costs money.
   */
  readonly executionEnabled: boolean;
  /** Whether the schedule fires. False everywhere, for the same reason. */
  readonly schedulerEnabled: boolean;
  /** Whether a real model provider may be invoked. */
  readonly allowBedrockCalls: boolean;
  /** How often the schedule would fire, if it were enabled. */
  readonly scheduleExpression: string;
  /**
   * The highest cycle this deployment will advance to.
   *
   * Below the protocol's own twenty-four in staging, deliberately: a mistake there
   * should cost a few cycles rather than a whole experiment's worth of generations.
   */
  readonly maximumCycles?: number;
  /** Whether this deployment may hold a canonical run. Only production, and only
   * when an operator sets it; the flag exists so the answer is never implicit. */
  readonly canonical: boolean;
  /** What happens to the table and the buckets when the stack goes away. */
  readonly removalPolicy: RemovalPolicy;
  /** Whether the table refuses to be deleted while it holds a run. */
  readonly deletionProtection: boolean;
  /** Whether point-in-time recovery is on for the table. */
  readonly pointInTimeRecovery: boolean;
  /** How long CloudWatch keeps a log group's lines. */
  readonly logRetention: RetentionDays;
  /** Browser origins the read API answers. Never a wildcard. */
  readonly allowedOrigins: readonly string[];
  /** The run this deployment serves and would advance. */
  readonly runId: string;
}

const BASE = {
  executionEnabled: false,
  schedulerEnabled: false,
  canonical: false,
  // Hourly. Slow enough that a scheduler left on overnight costs a day of cycles
  // rather than a month's budget, and fast enough to finish a pilot in a day.
  scheduleExpression: 'rate(1 hour)',
} as const;

export const ENVIRONMENTS: Record<EnvironmentName, EnvironmentConfig> = {
  local: {
    ...BASE,
    name: 'local',
    allowBedrockCalls: false,
    maximumCycles: 2,
    removalPolicy: RemovalPolicy.DESTROY,
    deletionProtection: false,
    pointInTimeRecovery: false,
    logRetention: RetentionDays.ONE_WEEK,
    allowedOrigins: ['http://localhost:5173', 'http://localhost:4173'],
    runId: 'run_local_pilot',
  },
  staging: {
    ...BASE,
    name: 'staging',
    // Armed at the stack level so a smoke test can run without a redeploy; the
    // handler still refuses unless AS_EXECUTION_ENABLED is also set, and that one
    // stays off until an operator turns it on.
    allowBedrockCalls: true,
    maximumCycles: 3,
    removalPolicy: RemovalPolicy.DESTROY,
    deletionProtection: false,
    pointInTimeRecovery: true,
    logRetention: RetentionDays.ONE_MONTH,
    allowedOrigins: [],
    runId: 'run_aws_staging',
  },
  production: {
    ...BASE,
    name: 'production',
    allowBedrockCalls: true,
    // No ceiling: the protocol's own twenty-four is the limit, and a second number
    // here would be a second place for the experiment's length to be defined.
    removalPolicy: RemovalPolicy.RETAIN,
    deletionProtection: true,
    pointInTimeRecovery: true,
    logRetention: RetentionDays.ONE_YEAR,
    allowedOrigins: [],
    runId: 'run_aws_canonical',
  },
};

/**
 * Resolve an environment by name.
 *
 * @throws Error when the name is not one of the three. A typo must not silently
 * resolve to the most permissive configuration, or to any configuration at all.
 */
export function environmentConfig(name: string | undefined): EnvironmentConfig {
  const candidate = (name ?? 'local').trim().toLowerCase();
  if (!Object.hasOwn(ENVIRONMENTS, candidate)) {
    throw new Error(
      `unknown deployment environment ${JSON.stringify(candidate)}; ` +
        `expected one of ${Object.keys(ENVIRONMENTS).join(', ')}`,
    );
  }
  return ENVIRONMENTS[candidate as EnvironmentName];
}
