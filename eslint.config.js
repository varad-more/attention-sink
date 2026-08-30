// Flat config shared by every workspace. Type-aware linting is enabled because the
// rules that actually catch bugs (floating promises, unsafe any) need type info.
import js from '@eslint/js';
import tseslint from 'typescript-eslint';

export default tseslint.config(
  {
    // `.venv/` is ignored for the same reason as `node_modules/`: it holds third-party
    // code, and a Python dependency that happens to ship a JavaScript file is not this
    // repository's to lint.
    ignores: [
      '**/dist/**',
      '**/cdk.out/**',
      '**/node_modules/**',
      '**/coverage/**',
      '.venv/**',
      '**/*.d.ts',
    ],
  },
  js.configs.recommended,
  ...tseslint.configs.strictTypeChecked,
  {
    languageOptions: {
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
  },
  {
    // The exhibition renders numbers into strings constantly -- cycle counts, token
    // budgets, distances. `restrict-template-expressions` is there to stop `[object
    // Object]` reaching a user, and a number in a template is never that.
    files: ['apps/web/**/*.{ts,tsx}'],
    rules: {
      '@typescript-eslint/restrict-template-expressions': ['error', { allowNumber: true }],
    },
  },
  {
    files: ['apps/web/e2e/**/*.ts', '**/*.test.ts', '**/*.test.tsx'],
    rules: {
      // A test asserting on a value the types call impossible is a test asserting the
      // types are right, which is exactly what a test should be free to do.
      '@typescript-eslint/no-unnecessary-condition': 'off',
    },
  },
  {
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
);
