/**
 * Build-time configuration, validated once at module load.
 *
 * Two rules shape everything here. A client that does not know where its API is has
 * nothing honest to render, so a production-like build fails loudly rather than
 * quietly showing an empty exhibition. And an unconfigured client is, by definition,
 * not showing verified data, so every default fails towards "this is simulated" --
 * the opposite default would let fixture output pass as canonical.
 */

/** Where the application believes it is running. */
export type DeploymentMode = 'local' | 'staging' | 'production';

const DEPLOYMENT_MODES: readonly DeploymentMode[] = ['local', 'staging', 'production'];

export interface AppConfig {
  /** Base URL of the read API. No trailing slash. */
  readonly apiBaseUrl: string;
  /** The run this exhibition shows. */
  readonly runId: string;
  readonly deploymentMode: DeploymentMode;
  /** How often a live view re-asks the API, in milliseconds. */
  readonly pollIntervalMs: number;
  /**
   * Whether the data behind this client was produced by fixtures.
   *
   * Drives a permanent banner, not a console message. A reader must never have to
   * guess whether what they are looking at actually happened.
   */
  readonly fixtureMode: boolean;
}

export class ConfigurationError extends Error {}

function mode(raw: string | undefined): DeploymentMode {
  const candidate = (raw ?? '').trim().toLowerCase();
  return DEPLOYMENT_MODES.find((known) => known === candidate) ?? 'local';
}

function positiveInteger(raw: string | undefined, fallback: number): number {
  const parsed = Number.parseInt((raw ?? '').trim(), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

/**
 * Whether fixture mode is on.
 *
 * Absent means true. Turning it *off* has to be deliberate, because the flag is what
 * removes the simulated banner, and a banner lost to a missing variable is worse
 * than a banner shown on a real run.
 */
function fixtureMode(raw: string | undefined, deployment: DeploymentMode): boolean {
  const candidate = (raw ?? '').trim().toLowerCase();
  if (candidate === 'false' || candidate === '0') return false;
  if (candidate === 'true' || candidate === '1') return true;
  return deployment !== 'production';
}

/**
 * Resolve configuration from a Vite-style environment.
 *
 * @throws ConfigurationError when a non-local build has no API base URL or run id.
 */
export function resolveConfig(env: Record<string, string | undefined>): AppConfig {
  const deploymentMode = mode(env.VITE_DEPLOYMENT_MODE);
  const apiBaseUrl = (env.VITE_API_BASE_URL ?? '').trim().replace(/\/+$/, '');
  const runId = (env.VITE_PUBLIC_RUN_ID ?? '').trim();

  if (deploymentMode !== 'local') {
    const missing = [
      apiBaseUrl ? null : 'VITE_API_BASE_URL',
      runId ? null : 'VITE_PUBLIC_RUN_ID',
    ].filter((name): name is string => name !== null);
    if (missing.length > 0) {
      throw new ConfigurationError(
        `a ${deploymentMode} build needs ${missing.join(' and ')}; refusing to start a client ` +
          'that does not know which run it is showing',
      );
    }
  }

  return {
    apiBaseUrl: apiBaseUrl || 'http://localhost:8000',
    runId: runId || 'run_local_pilot',
    deploymentMode,
    pollIntervalMs: positiveInteger(env.VITE_POLL_INTERVAL_MS, 5000),
    fixtureMode: fixtureMode(env.VITE_FIXTURE_MODE, deploymentMode),
  };
}

/** The configuration this build was compiled with. */
export function currentConfig(): AppConfig {
  return resolveConfig(import.meta.env);
}
