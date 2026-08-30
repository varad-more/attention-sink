/**
 * Where the Python deployment package is, and what to say when it is not there.
 *
 * The bundle is built by `make aws-bundle`, not by CDK. Bundling inside synthesis
 * would mean either a Docker daemon or a network fetch on every `cdk synth`, and
 * `make synth` has to run on a laptop with neither. Building it as a separate,
 * inspectable step also means the thing that gets deployed is a directory somebody
 * can look at before it goes anywhere.
 */
import { existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));

/** The repository root, from this file's own location. */
export const REPO_ROOT = join(HERE, '..', '..', '..');

/** Where `make aws-bundle` writes the Python deployment package. */
export const LAMBDA_BUNDLE_DIR = join(REPO_ROOT, 'dist', 'lambda');

/** Where `npm run build --workspace apps/web` writes the exhibition. */
export const WEB_DIST_DIR = join(REPO_ROOT, 'apps', 'web', 'dist');

/**
 * The bundle directory, checked.
 *
 * @throws Error naming the command to run. A missing bundle otherwise surfaces as
 * an asset-staging error several frames deep, which sends the reader looking at CDK
 * rather than at the one command they have not run.
 */
export function lambdaBundleDir(): string {
  if (!existsSync(join(LAMBDA_BUNDLE_DIR, 'attention_sink'))) {
    throw new Error(
      `no Lambda bundle at ${LAMBDA_BUNDLE_DIR}. Run \`make aws-bundle\` first: ` +
        'the deployment package is built as its own step so that synthesis needs ' +
        'neither Docker nor a network.',
    );
  }
  return LAMBDA_BUNDLE_DIR;
}

/**
 * Whether a built frontend is present to upload.
 *
 * The first deployment of a new stack has none: the exhibition is compiled against
 * the API's URL, and that URL does not exist until the API does. `make aws-deploy`
 * therefore deploys, reads the URL, builds, and deploys again -- and this is what
 * makes the first of those two passes possible.
 */
export function webDistPresent(): boolean {
  return existsSync(join(WEB_DIST_DIR, 'index.html'));
}
