/** Verifies typed preview filters, pending controls, and UTC snapshot timestamps. */
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'
import {
  DataTableCommandFilterMenu,
  assetFilterOperators,
  assetFilterValueKind,
  parseAssetFilterDraft,
  previewFilterLabel,
} from './asset-data-filter'
import { formatSnapshotTimestamp } from './asset-tabs'

describe('asset data filter labels', () => {
  it('shows the column, operator, and literal value', () => {
    expect(
      previewFilterLabel({ column: 'health_key', operator: 'eq', value: 'ok' }),
    ).toBe('health_key is ok')
    expect(
      previewFilterLabel({ column: 'row_count', operator: 'gte', value: 0 }),
    ).toBe('row_count at least 0')
    expect(
      previewFilterLabel({ column: 'active', operator: 'eq', value: false }),
    ).toBe('active is false')
  })

  it('labels null predicates without inventing a value', () => {
    expect(
      previewFilterLabel({ column: 'health_key', operator: 'is_null' }),
    ).toBe('health_key is null')
  })
})

describe('asset filter metadata and predicates', () => {
  const columns = [
    { name: 'health_key', type: 'varchar' },
    { name: 'row_count', type: 'bigint' },
    { name: 'active', type: 'boolean' },
  ]

  it('infers input kinds from observed column types', () => {
    expect(assetFilterValueKind('DECIMAL(18, 2)')).toBe('number')
    expect(assetFilterValueKind('boolean')).toBe('boolean')
    expect(assetFilterValueKind('timestamp with time zone')).toBe('text')
    expect(assetFilterValueKind(null)).toBe('text')
  })

  it('exposes only the server-supported comparisons for each type', () => {
    expect(assetFilterOperators('varchar').map(({ value }) => value)).toEqual([
      'eq',
      'ne',
      'is_null',
      'is_not_null',
    ])
    expect(assetFilterOperators('bigint').map(({ value }) => value)).toEqual([
      'eq',
      'ne',
      'lt',
      'lte',
      'gt',
      'gte',
      'is_null',
      'is_not_null',
    ])
    expect(assetFilterOperators('boolean').map(({ value }) => value)).toEqual([
      'eq',
      'ne',
      'is_null',
      'is_not_null',
    ])
  })

  it('preserves numeric zero and boolean false as typed predicates', () => {
    expect(
      parseAssetFilterDraft(
        { column: 'row_count', operator: 'eq', value: '0' },
        columns,
      ),
    ).toEqual({ column: 'row_count', operator: 'eq', value: 0 })
    expect(
      parseAssetFilterDraft(
        { column: 'active', operator: 'eq', value: 'false' },
        columns,
      ),
    ).toEqual({ column: 'active', operator: 'eq', value: false })
  })

  it('keeps null predicates valueless and distinct from empty text', () => {
    expect(
      parseAssetFilterDraft(
        { column: 'health_key', operator: 'is_null', value: '' },
        columns,
      ),
    ).toEqual({ column: 'health_key', operator: 'is_null' })
    expect(
      parseAssetFilterDraft(
        { column: 'health_key', operator: 'eq', value: '' },
        columns,
      ),
    ).toEqual({ column: 'health_key', operator: 'eq', value: '' })
  })

  it('locks chip drafts while the server is applying a predicate', () => {
    const html = renderToStaticMarkup(
      <DataTableCommandFilterMenu
        columns={columns}
        filters={[{ column: 'row_count', operator: 'eq', value: 12 }]}
        pending
        errorMessage={null}
        onApply={() => Promise.resolve(false)}
        onValidationError={() => {}}
        onOpenChange={() => {}}
      />,
    )
    for (const label of [
      'Value for row_count',
      'Operator for row_count',
      'Change field row_count',
    ]) {
      expect(html).toMatch(
        new RegExp(
          `<[^>]*(?=[^>]*aria-label="${label}")(?=[^>]*disabled="")[^>]*>`,
        ),
      )
    }
  })

  it('rejects invalid numbers, booleans, and fields', () => {
    expect(
      parseAssetFilterDraft(
        { column: 'row_count', operator: 'gte', value: '' },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'row_count', operator: 'gte', value: '1e400' },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'row_count', operator: 'eq', value: '0x10' },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'health_key', operator: 'lt', value: 'x' },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'health_key', operator: 'eq', value: 'x'.repeat(2049) },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'active', operator: 'eq', value: 'maybe' },
        columns,
      ),
    ).toBeNull()
    expect(
      parseAssetFilterDraft(
        { column: 'unknown', operator: 'eq', value: 'x' },
        columns,
      ),
    ).toBeNull()
  })
})

describe('snapshot timestamp display', () => {
  it('renders a readable UTC date and time independent of local timezone', () => {
    expect(
      formatSnapshotTimestamp(Date.parse('2026-05-02T14:05:06.000Z')),
    ).toBe('2 May 2026, 14:05:06 UTC')
  })

  it('reports invalid timestamps explicitly', () => {
    expect(formatSnapshotTimestamp(Number.NaN)).toBe('Timestamp unavailable')
  })
})
