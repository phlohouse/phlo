// Remove once these plugins support minimatch 11. Keep matching on its bounded
// matcher rather than restoring the removed regular-expression compiler.
import { readFileSync, writeFileSync } from 'node:fs'

const patches = [
  ...['mayContainChildComponent', 'mayHaveAccessibleLabel'].map((name) => [
    `eslint-plugin-jsx-a11y/lib/util/${name}.js`,
    'var _minimatch = _interopRequireDefault(require("minimatch"));',
    'var _minimatch = { default: require("minimatch").minimatch };',
  ]),
  [
    'eslint-plugin-import-x/lib/rules/no-internal-modules.js',
    "import { makeRe } from 'minimatch';",
    "import { Minimatch } from 'minimatch';",
  ],
  [
    'eslint-plugin-import-x/lib/rules/no-internal-modules.js',
    '.map(p => makeRe(p))',
    '.map(p => new Minimatch(p))',
  ],
  [
    'eslint-plugin-import-x/lib/rules/no-internal-modules.js',
    're.test(importPath)',
    're.match(importPath)',
  ],
]

for (const [path, before, after] of patches) {
  const file = new URL(`../node_modules/${path}`, import.meta.url)
  const source = readFileSync(file, 'utf8')
  if (source.includes(before)) {
    writeFileSync(file, source.replaceAll(before, after))
  } else if (!source.includes(after)) {
    throw new Error(`Review minimatch compatibility patch for ${path}`)
  }
}
