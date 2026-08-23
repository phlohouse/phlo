/** Tests theme mode resolution and snapshot behavior across light/dark/system. */
import { describe, expect, test } from 'vitest'

import {
  OBSERVATORY_THEME_STORAGE_KEY,
  getObservatoryThemeSnapshot,
  resolveObservatoryTheme,
} from './theme'

describe('resolveObservatoryTheme', () => {
  test('uses system preference when mode is system', () => {
    expect(resolveObservatoryTheme('system', true)).toBe('dark')
    expect(resolveObservatoryTheme('system', false)).toBe('light')
  })

  test('keeps explicit light and dark modes independent of system preference', () => {
    expect(resolveObservatoryTheme('light', true)).toBe('light')
    expect(resolveObservatoryTheme('dark', false)).toBe('dark')
  })
})

describe('getObservatoryThemeSnapshot', () => {
  test('resolves stored dark before the first component effect', () => {
    const storage = {
      getItem: (key: string) =>
        key === OBSERVATORY_THEME_STORAGE_KEY ? 'dark' : null,
    } satisfies Pick<Storage, 'getItem'>

    expect(getObservatoryThemeSnapshot(storage, false)).toEqual({
      mode: 'dark',
      resolvedTheme: 'dark',
      systemPrefersDark: false,
    })
  })
})
