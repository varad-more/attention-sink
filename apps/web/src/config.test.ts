import { describe, expect, it } from 'vitest';

import { ConfigurationError, resolveConfig } from './config';

describe('configuration', () => {
  it('defaults an unconfigured build to local and simulated', () => {
    const config = resolveConfig({});
    expect(config.deploymentMode).toBe('local');
    expect(config.fixtureMode).toBe(true);
    expect(config.apiBaseUrl).toBe('http://localhost:8000');
    expect(config.runId).toBe('run_local_pilot');
  });

  it('refuses a production build with no API base URL or run id', () => {
    expect(() => resolveConfig({ VITE_DEPLOYMENT_MODE: 'production' })).toThrow(ConfigurationError);
    expect(() =>
      resolveConfig({ VITE_DEPLOYMENT_MODE: 'production', VITE_API_BASE_URL: 'https://api.test' }),
    ).toThrow(/VITE_PUBLIC_RUN_ID/);
  });

  it('accepts a fully configured production build', () => {
    const config = resolveConfig({
      VITE_DEPLOYMENT_MODE: 'production',
      VITE_API_BASE_URL: 'https://api.test/',
      VITE_PUBLIC_RUN_ID: 'run_canonical',
      VITE_FIXTURE_MODE: 'false',
    });
    expect(config.apiBaseUrl).toBe('https://api.test');
    expect(config.fixtureMode).toBe(false);
  });

  it('keeps the simulated banner unless fixture mode is explicitly turned off', () => {
    // A banner lost to a missing variable is worse than one shown on a real run.
    expect(
      resolveConfig({
        VITE_DEPLOYMENT_MODE: 'staging',
        VITE_API_BASE_URL: 'https://a',
        VITE_PUBLIC_RUN_ID: 'r',
      }).fixtureMode,
    ).toBe(true);
    expect(
      resolveConfig({
        VITE_DEPLOYMENT_MODE: 'production',
        VITE_API_BASE_URL: 'https://a',
        VITE_PUBLIC_RUN_ID: 'r',
      }).fixtureMode,
    ).toBe(false);
  });

  it('falls back to a sane poll interval rather than zero', () => {
    expect(resolveConfig({ VITE_POLL_INTERVAL_MS: 'soon' }).pollIntervalMs).toBe(5000);
    expect(resolveConfig({ VITE_POLL_INTERVAL_MS: '-2' }).pollIntervalMs).toBe(5000);
    expect(resolveConfig({ VITE_POLL_INTERVAL_MS: '750' }).pollIntervalMs).toBe(750);
  });
});
