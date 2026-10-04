/** Verifies exact row totals retain decimal precision and snapshot identity. */
import { describe, expect, it } from 'vitest'
import { exactRowCountValue, isExactRowCountCurrent } from './exactRowCount'

describe('exact row-count evidence', () => {
  it('preserves zero and large decimal totals without number conversion', () => {
    expect(exactRowCountValue('0')).toBe('0')
    expect(exactRowCountValue('9007199254740993')).toBe('9007199254740993')
    expect(exactRowCountValue(0)).toBeNull()
    expect(exactRowCountValue('-1')).toBeNull()
  })

  it('only treats a count as current when its snapshot identity matches', () => {
    expect(isExactRowCountCurrent('0', '42', '42')).toBe(true)
    expect(isExactRowCountCurrent('12', '43', '42')).toBe(false)
    expect(isExactRowCountCurrent('12', null, '42')).toBe(false)
    expect(isExactRowCountCurrent(null, '42', '42')).toBe(false)
  })
})
