//  @ts-check

import { tanstackConfig } from '@tanstack/eslint-config'
import jsxA11y from 'eslint-plugin-jsx-a11y'

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
  jsxA11y.flatConfigs.recommended,
  {
    rules: {
      '@typescript-eslint/no-unnecessary-condition': 'off',
      complexity: ['error', 15],
      'jsx-a11y/label-has-associated-control': [
        'error',
        { controlComponents: ['Input', 'Select', 'Textarea'] },
      ],
      'jsx-a11y/no-noninteractive-tabindex': [
        'error',
        { roles: ['region', 'table', 'tabpanel'] },
      ],
    },
  },
]
