import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./vitest.setup.ts'],
    // Playwright owns `e2e/`. Vitest would collect those specs and fail on the first
    // `test()` call, which says nothing about either suite.
    exclude: ['e2e/**', 'node_modules/**', 'dist/**'],
  },
});
