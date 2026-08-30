/**
 * Playwright against the real local stack.
 *
 * No mock server and no fixture JSON: the flows run against the same API the
 * exhibition uses, so a test that passes is evidence the product works rather than
 * evidence the mocks agree with themselves. The API and the run must already exist —
 * `make pilot-local-e2e` is preceded by `make local-all` in the release check.
 */

import { defineConfig, devices } from '@playwright/test';

const PORT = Number(process.env.E2E_PORT ?? 4173);
const API = process.env.VITE_API_BASE_URL ?? 'http://localhost:8000';

export default defineConfig({
  testDir: './e2e',
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['list'], ['json', { outputFile: 'e2e-results.json' }]] : 'list',
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop', use: { ...devices['Desktop Chrome'] } },
    { name: 'mobile', use: { ...devices['Pixel 7'] } },
  ],
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
});
