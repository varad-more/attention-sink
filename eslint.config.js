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
    files: ['**/*.js'],
    ...tseslint.configs.disableTypeChecked,
  },
);
