import { App } from 'aws-cdk-lib';
import { Template } from 'aws-cdk-lib/assertions';
import { describe, expect, it } from 'vitest';

import { FoundationStack } from '../lib/foundation-stack.js';

function declaredResourceIds(): string[] {
  const stack = new FoundationStack(new App(), 'TestStack');
  const template = Template.fromStack(stack).toJSON() as { Resources?: Record<string, unknown> };
  // CDKMetadata is emitted by the toolkit itself, not declared by this stack.
  return Object.keys(template.Resources ?? {}).filter((key) => key !== 'CDKMetadata');
}

describe('FoundationStack', () => {
  it('synthesises without an account or Region, so CI needs no AWS credentials', () => {
    expect(declaredResourceIds).not.toThrow();
  });

  it('declares no resources yet, so nothing was scaffolded before it was designed', () => {
    expect(declaredResourceIds()).toEqual([]);
  });
});
