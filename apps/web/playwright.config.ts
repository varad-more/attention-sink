/**
 * Playwright against a real stack, local or deployed.
 *
 * No mock server and no fixture JSON: the flows run against the same API the
 * exhibition uses, so a test that passes is evidence the product works rather than
 * evidence the mocks agree with themselves. The API and the run must already exist —
 * `make pilot-local-e2e` is preceded by `make local-all` in the release check.
 *
 * Setting `E2E_BASE_URL` points the same fourteen flows at a deployed site instead:
 * no local build, no local preview server, and nothing about the page under test
 * supplied by this file. That is what makes it a release check rather than a rehearsal
 * — the bytes CloudFront serves are the bytes the flows run against.
 */

import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 4173);
const API = process.env.VITE_API_BASE_URL ?? 'http://localhost:8000';
const DEPLOYED = process.env.E2E_BASE_URL?.trim();

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['json', { outputFile: 'e2e-results.json' }]] : 'list',
  use: {
    baseURL: DEPLOYED ?? `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
  },
  // A deployed page waits on CloudFront, an API Gateway, and a Lambda that may be
  // cold. Locally none of that exists, and a generous timeout would only make a
  // genuine hang take longer to report.
  expect: { timeout: DEPLOYED ? 20_000 : 5_000 },
  timeout: DEPLOYED ? 90_000 : 30_000,
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
  ...(DEPLOYED
    ? {}
    : {
        webServer: {
          command: `npm run build && npm run preview -- --port ${PORT} --strictPort`,
          port: PORT,
          reuseExistingServer: !process.env.CI,
          timeout: 180_000,
          env: {
            VITE_API_BASE_URL: API,
            VITE_PUBLIC_RUN_ID: process.env.VITE_PUBLIC_RUN_ID ?? 'run_local_pilot',
            VITE_DEPLOYMENT_MODE: 'local',
            VITE_FIXTURE_MODE: 'true',
            VITE_POLL_INTERVAL_MS: '30000',
          },
        },
      }),
});
