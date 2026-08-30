#!/usr/bin/env node
import { App, type Environment, Tags } from 'aws-cdk-lib';

import { environmentConfig } from '../lib/environments.js';
import { PilotStack, type ModelIdentifiers } from '../lib/pilot-stack.js';

/**
 * Read the target account and Region from the ambient CDK environment.
 *
 * Never from source: a hardcoded account in an infrastructure repository is both a
 * leak and a portability failure. Returning `undefined` when neither is set leaves
 * the stack environment-agnostic, which is what lets `make synth` run in CI and on a
 * laptop that has never been given AWS credentials.
 */
function ambientEnvironment(): Environment | undefined {
  const account = process.env.CDK_DEFAULT_ACCOUNT;
  const region = process.env.CDK_DEFAULT_REGION;
  if (!account && !region) {
    return undefined;
  }
  return {
    ...(account ? { account } : {}),
    ...(region ? { region } : {}),
  };
}

/**
 * The five model identifiers, or nothing.
 *
 * The same environment variables the Python gateway reads, so a deployment and the
 * process it deploys cannot disagree about which models a run used. Absent is a
 * supported state: no identifier is compiled in (ADR-006), and a stack deployed
 * without them grants no Bedrock permission and produces functions that refuse to
 * start rather than reaching for a default nobody recorded.
 */
export function modelIdentifiers(
  env: NodeJS.ProcessEnv = process.env,
): ModelIdentifiers | undefined {
  const resolved = {
    writerModelId: (env.WRITER_MODEL_ID ?? '').trim(),
    auditorModelId: (env.AUDITOR_MODEL_ID ?? '').trim(),
    judgeModelId: (env.JUDGE_MODEL_ID ?? '').trim(),
    summaryModelId: (env.SUMMARY_MODEL_ID ?? '').trim(),
    embeddingModelId: (env.EMBEDDING_MODEL_ID ?? '').trim(),
  };
  return Object.values(resolved).every((value) => value.length > 0) ? resolved : undefined;
}

const app = new App();
const env = ambientEnvironment();
const config = environmentConfig(
  (app.node.tryGetContext('environment') as string | undefined) ??
    process.env.AS_DEPLOYMENT_ENVIRONMENT,
);

const models = modelIdentifiers();

/**
 * Which counter the deployment declares, from the same variable the gateway reads.
 *
 * Defaults to `bedrock`. Every other value must be spelled exactly, so a typo
 * deploys an exact counter rather than quietly deploying the approximate one.
 */
export function tokenCountSourceOf(
  env: NodeJS.ProcessEnv = process.env,
): 'bedrock' | 'converse' | 'heuristic' {
  const declared = env.TOKEN_COUNT_SOURCE?.trim();
  return declared === 'heuristic' || declared === 'converse' ? declared : 'bedrock';
}

const tokenCountSource = tokenCountSourceOf();

/** The commit this bundle was built from, when the deploying process knows one. */
const gitCommit = process.env.AS_GIT_COMMIT?.trim() ?? '';

/**
 * Browser origins the read API answers, from the same variable the API reads.
 *
 * Empty on a new stack's first deployment: the exhibition's origin is the
 * distribution's domain, and that does not exist yet. `make aws-deploy` reads it from
 * the stack outputs and deploys a second time with it set.
 */
const allowedOrigins = (process.env.AS_ALLOWED_ORIGINS ?? '')
  .split(',')
  .map((origin) => origin.trim())
  .filter((origin) => origin.length > 0);

const stack = new PilotStack(app, `AttentionSink-${config.name}`, {
  ...(env ? { env } : {}),
  description: `Attention Sink pilot (${config.name}).`,
  config,
  tokenCountSource,
  allowedOrigins,
  ...(gitCommit ? { gitCommit } : {}),
  ...(models ? { models } : {}),
});

Tags.of(stack).add('project', 'attention-sink');
Tags.of(stack).add('environment', config.name);
