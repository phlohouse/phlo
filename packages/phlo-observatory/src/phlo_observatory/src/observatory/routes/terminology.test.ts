/**
 * Source scan enforcing the Observatory glossary: fails when banned
 * user-facing terms appear outside the explicit allowlist.
 */
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { join, resolve } from 'node:path'

import { describe, expect, it } from 'vitest'

const sourceRoots = [
  resolve(import.meta.dirname, '../../routes'),
  resolve(import.meta.dirname, '..'),
]

const bannedUserFacingTerms = [
  /\bData Product\b/i,
  /\bdata-products\b/i,
  /\bdataProducts\b/,
  /\bdata_product\b/,
  /\bProduct readiness\b/i,
]

const allowedFiles = new Set([
  resolve(import.meta.dirname, 'extensions.test.ts'),
  resolve(import.meta.dirname, 'terminology.test.ts'),
])

function sourceFiles(root: string): Array<string> {
  return readdirSync(root).flatMap((entry) => {
    const filePath = join(root, entry)
    const stat = statSync(filePath)
    if (stat.isDirectory()) return sourceFiles(filePath)
    if (!/\.(ts|tsx|css)$/.test(entry)) return []
    if (allowedFiles.has(filePath)) return []
    return [filePath]
  })
}

describe('Observatory terminology', () => {
  it('does not reintroduce Data Product language in UI source', () => {
    const offenders = sourceRoots.flatMap(sourceFiles).flatMap((filePath) => {
      const source = readFileSync(filePath, 'utf8')
      return bannedUserFacingTerms
        .filter((term) => term.test(source))
        .map((term) => `${filePath}: ${term}`)
    })

    expect(offenders).toEqual([])
  })

  it('does not restore removed browser route files', () => {
    const routeRoot = resolve(import.meta.dirname, '../../routes')
    const removedRouteFiles = [
      'assets.tsx',
      'catalog.tsx',
      'data-products.tsx',
      'data.tsx',
    ]

    const present = removedRouteFiles.filter((fileName) =>
      sourceFiles(routeRoot).some((filePath) => filePath.endsWith(fileName)),
    )

    expect(present).toEqual([])
  })
})
