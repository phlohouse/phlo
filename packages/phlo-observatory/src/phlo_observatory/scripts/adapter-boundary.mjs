/** Enforces the API-only Node adapter boundary, not a pure-SPA runtime. */
import { readFileSync, readdirSync } from 'node:fs'
import { resolve } from 'node:path'
import ts from 'typescript'

export function checkSource(source, filename) {
  const tree = ts.createSourceFile(
    filename,
    source,
    ts.ScriptTarget.Latest,
    true,
  )
  const errors = []
  const adapter = filename.includes('/lib/data/api/')
  const transport = filename.endsWith('/lib/data/api/client.ts')
  function visit(node) {
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      const name = node.moduleSpecifier?.text
      if (
        name &&
        (name.startsWith('node:') ||
          /^(fs|http|https|net|tls|child_process|pg|postgres|mysql|better-sqlite3|sqlite3|@aws-sdk|@dagster|trino|nessie|minio)(\/|$)/.test(
            name,
          ))
      ) {
        errors.push(`Provider/database access is prohibited: ${name}`)
      }
      if (
        adapter &&
        name &&
        !name.startsWith('.') &&
        ![
          'zod',
          '@tanstack/react-start',
          '@tanstack/react-start/server',
        ].includes(name)
      ) {
        errors.push(`Backend dependency outside API adapters: ${name}`)
      }
      if (adapter && name?.startsWith('../'))
        errors.push(
          'Adapters must not import a parallel backend implementation',
        )
    }
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const name = node.expression.getText(tree)
      if (
        /\b(require|import)$/.test(name) ||
        (/\b(fetch|XMLHttpRequest|WebSocket)$/.test(name) && !transport)
      )
        errors.push(
          `Only the authenticated API transport may perform I/O: ${name}`,
        )
      if (name === 'createServerFn' && !adapter)
        errors.push(
          'Server responsibilities belong in authenticated API adapters',
        )
      if (name === 'phloApi' && node.arguments?.[0]) {
        const path = node.arguments[0].getText(tree)
        // Dynamic pagination's API base is independently resolved by the drift gate.
        if (!path.includes('api/v1/') && !path.includes('${base}'))
          errors.push(`Noncanonical backend endpoint: ${path}`)
      }
    }
    if (
      (ts.isPropertyAccessExpression(node) ||
        ts.isElementAccessExpression(node)) &&
      /^process\.env(?:\.|\[)/.test(node.getText(tree))
    ) {
      if (!transport || node.getText(tree) !== 'process.env.PHLO_API_URL')
        errors.push(
          'Adapters must not resolve provider configuration or shared credentials',
        )
    }
    if (
      ts.isStringLiteralLike(node) &&
      /\b(SELECT\s+.+\s+FROM|INSERT\s+INTO|CREATE\s+TABLE|UPDATE\s+.+\s+SET)\b/i.test(
        node.text,
      ) &&
      adapter
    ) {
      errors.push(
        'Backend SQL/source-of-truth logic is prohibited in API adapters',
      )
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  return errors
}

export function checkTree(directory) {
  const errors = []
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = resolve(directory, entry.name)
    if (entry.isDirectory()) errors.push(...checkTree(path))
    else if (/\.tsx?$/.test(path) && !/\.(test|spec)\.tsx?$/.test(path)) {
      errors.push(
        ...checkSource(readFileSync(path, 'utf8'), path).map(
          (error) => `${path}: ${error}`,
        ),
      )
    }
  }
  return errors
}
