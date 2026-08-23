/**
 * Theme mode resolution and persistence for the shell: resolves 'system'
 * against prefers-color-scheme and reads/writes the stored mode.
 */
export type ObservatoryThemeMode = 'system' | 'light' | 'dark'
export type ObservatoryResolvedTheme = 'light' | 'dark'

export const OBSERVATORY_THEME_STORAGE_KEY = 'phlo-observatory-theme'

export type ObservatoryThemeSnapshot = {
  mode: ObservatoryThemeMode
  resolvedTheme: ObservatoryResolvedTheme
  systemPrefersDark: boolean
}

export function resolveObservatoryTheme(
  mode: ObservatoryThemeMode,
  systemPrefersDark: boolean,
): ObservatoryResolvedTheme {
  if (mode === 'system') return systemPrefersDark ? 'dark' : 'light'
  return mode
}

export function readObservatoryThemeMode(
  storage: Pick<Storage, 'getItem'>,
): ObservatoryThemeMode {
  const stored = storage.getItem(OBSERVATORY_THEME_STORAGE_KEY)
  if (stored === 'light' || stored === 'dark' || stored === 'system') {
    return stored
  }
  return 'system'
}

export function getObservatoryThemeSnapshot(
  storage: Pick<Storage, 'getItem'>,
  systemPrefersDark: boolean,
): ObservatoryThemeSnapshot {
  const mode = readObservatoryThemeMode(storage)
  return {
    mode,
    resolvedTheme: resolveObservatoryTheme(mode, systemPrefersDark),
    systemPrefersDark,
  }
}
