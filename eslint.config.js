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
    // Standalone Node scripts, owned by no tsconfig. Type-aware rules need a project
    // that includes the file, so these get the rules that work without type
    // information and nothing else -- which still catches what matters in a build
    // script: an unused variable, unreachable code, a mistyped global.
    files: ['scripts/**/*.mjs'],
    ...tseslint.configs.disableTypeChecked,
    languageOptions: {
      parserOptions: { projectService: false, project: null },
      globals: {
        console: 'readonly',
        fetch: 'readonly',
        process: 'readonly',
        URL: 'readonly',
        // `document` and `window` appear inside `page.evaluate()` callbacks, which are
        // serialised and run in the browser rather than in Node. They are real globals
        // where those functions execute.
        document: 'readonly',
        window: 'readonly',
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
    // A CloudFormation template is untyped JSON, and CDK's own assertions API says so:
    // `findResources` returns `Record<string, Record<string, any>>`. Reaching into one
    // is the whole activity here, and the `expect` on the next line is the check the
    // `no-unsafe-*` rules would otherwise be standing in for. Scoped to this one file
    // so the rules stay on everywhere they can catch something.
    files: ['infrastructure/cdk/test/**/*.test.ts'],
    rules: {
      '@typescript-eslint/no-unsafe-member-access': 'off',
      '@typescript-eslint/no-unsafe-assignment': 'off',
      '@typescript-eslint/no-unsafe-argument': 'off',
      '@typescript-eslint/no-unsafe-call': 'off',
      '@typescript-eslint/no-unsafe-return': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
    },
  },
  {
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
);
