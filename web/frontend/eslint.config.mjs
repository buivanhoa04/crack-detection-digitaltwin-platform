import { defineConfig, globalIgnores } from 'eslint/config';
import nextVitals from 'eslint-config-next/core-web-vitals';

export default defineConfig([
  ...nextVitals,
  {
    rules: {
      // Existing screens intentionally fetch/initialize state from effects.
      // Keep the newer React compiler rules visible without blocking CI while
      // those components are incrementally refactored.
      'react-hooks/set-state-in-effect': 'off',
      'react-hooks/refs': 'warn',
      'react-hooks/immutability': 'warn',
      'react/no-unescaped-entities': 'warn',
      '@next/next/no-html-link-for-pages': 'warn',
    },
  },
  globalIgnores([
    '.next/**',
    'out/**',
    'build/**',
    'next-env.d.ts',
  ]),
]);
