import assert from 'node:assert/strict'
import { test } from 'node:test'
import { ESLint } from 'eslint'
import importX from 'eslint-plugin-import-x'
import jsxA11y from 'eslint-plugin-jsx-a11y'

test('accessibility rules match custom controls with minimatch 11', async () => {
  const eslint = new ESLint({
    overrideConfigFile: true,
    overrideConfig: [
      {
        files: ['**/*.jsx'],
        languageOptions: { parserOptions: { ecmaFeatures: { jsx: true } } },
        plugins: { 'jsx-a11y': jsxA11y },
        rules: {
          'jsx-a11y/label-has-associated-control': [
            'error',
            {
              controlComponents: ['Custom*'],
            },
          ],
        },
      },
    ],
  })
  const [valid] = await eslint.lintText(
    '<label>Account<CustomInput /></label>',
    { filePath: 'fixture.jsx' },
  )
  assert.deepEqual(valid.messages, [])
  const [invalid] = await eslint.lintText('<label><CustomInput /></label>', {
    filePath: 'fixture.jsx',
  })
  assert.deepEqual(
    invalid.messages.map(({ ruleId }) => ruleId),
    ['jsx-a11y/label-has-associated-control'],
  )
})

test('import restrictions use bounded glob matching', async () => {
  const eslint = new ESLint({
    overrideConfigFile: true,
    overrideConfig: [
      {
        plugins: { 'import-x': importX },
        rules: {
          'import-x/no-internal-modules': [
            'error',
            { forbid: ['**/private/**'] },
          ],
        },
      },
    ],
  })
  const [result] = await eslint.lintText(
    "import 'example/public/value';\nimport 'example/private/value';",
    { filePath: 'fixture.js' },
  )
  assert.deepEqual(
    result.messages.map(({ ruleId, line }) => ({ ruleId, line })),
    [{ ruleId: 'import-x/no-internal-modules', line: 2 }],
  )
})
