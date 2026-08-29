import { Stack } from 'aws-cdk-lib';

/**
 * The root stack of the experiment.
 *
 * Intentionally declares no resources yet. Its job in this phase is to prove that
 * the synthesis path works end to end - CLI, TypeScript build, assertions - before
 * any resource exists whose shape would be expensive to change later. Resources
 * arrive with the phase that needs them, each scoped to the permissions it actually
 * requires rather than a permissive set inherited from scaffolding.
 *
 * Synthesis therefore emits a CloudFormation validation warning that the template
 * has no `Resources` section. That warning is correct: this stack is not deployable
 * yet, and saying so out loud is better than suppressing it. It disappears with the
 * first resource, along with this note.
 */
export class FoundationStack extends Stack {}
