//  @ts-check

import { tanstackConfig } from '@tanstack/eslint-config'

export default [
  {
    ignores: [
      '**/.output/**',
      '**/dist/**',
      '**/node_modules/**',
      '**/eslint.config.js',
      '**/prettier.config.js',
    ],
  },
  ...tanstackConfig,
  {
    rules: {
      '@typescript-eslint/no-unnecessary-condition': 'off',
      complexity: ['error', 15],
    },
  },
]
