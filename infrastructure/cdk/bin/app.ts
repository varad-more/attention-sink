#!/usr/bin/env node
import { App, type Environment, Tags } from 'aws-cdk-lib';

import { FoundationStack } from '../lib/foundation-stack.js';

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

const app = new App();
const env = ambientEnvironment();

const stack = new FoundationStack(app, 'AttentionSinkFoundation', {
  ...(env ? { env } : {}),
  description: 'Attention Sink foundation stack.',
});

Tags.of(stack).add('project', 'attention-sink');
