/**
 * Observatory settings state. Hydrates from localStorage immediately, then
 * reconciles with server settings and defaults: server values win unless the
 * local copy is newer, in which case it is pushed back. Writes go to both
 * stores; on server failure the fallback defaults are used.
 */
import {
  createContext,
  use,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react'

import type { ObservatorySettings } from '@/lib/observatorySettings'
import {
  getFallbackObservatorySettings,
  loadStoredObservatorySettings,
  parseObservatorySettings,
  storeObservatorySettings,
} from '@/lib/observatorySettings'
import {
  getObservatorySettings,
  getObservatorySettingsDefaults,
  putObservatorySettings,
} from '@/observatory/api/settings'

type ObservatorySettingsContextValue = {
  settings: ObservatorySettings
  defaults: ObservatorySettings
  setSettings: (next: ObservatorySettings) => void
  resetToDefaults: () => void
}

const ObservatorySettingsContext =
  createContext<ObservatorySettingsContextValue | null>(null)

type StoredSettingsState = ReturnType<typeof loadStoredObservatorySettings>
type SettingsState = StoredSettingsState & {
  defaults: ObservatorySettings
}

type SettingsAction =
  | { type: 'defaults'; defaults: ObservatorySettings }
  | { type: 'settings'; stored: StoredSettingsState }
  | { type: 'replace'; settings: ObservatorySettings }

function settingsReducer(
  state: SettingsState,
  action: SettingsAction,
): SettingsState {
  switch (action.type) {
    case 'defaults':
      return { ...state, defaults: action.defaults }
    case 'settings':
      return { ...state, ...action.stored }
    case 'replace':
      return {
        ...state,
        settings: action.settings,
        source: 'localStorage',
      }
  }
}

export function ObservatorySettingsProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const fallback = useMemo(() => getFallbackObservatorySettings(), [])
  const [{ settings, defaults, source }, dispatchSettings] = useReducer(
    settingsReducer,
    fallback,
    (initialDefaults): SettingsState => ({
      ...loadStoredObservatorySettings(),
      defaults: initialDefaults,
    }),
  )
  const initialSettingsRef = useRef({ settings, source })

  useEffect(() => {
    let active = true
    Promise.all([
      getObservatorySettingsDefaults(),
      getObservatorySettings({ data: {} }),
    ])
      .then(([serverDefaults, serverSettings]) => {
        if (!active) return
        dispatchSettings({ type: 'defaults', defaults: serverDefaults })

        if (serverSettings.settings) {
          const parsed = parseObservatorySettings(
            serverSettings.settings,
            serverDefaults,
          )
          storeObservatorySettings(parsed)
          dispatchSettings({
            type: 'settings',
            stored: { settings: parsed, source: 'localStorage' },
          })
          return
        }

        if (initialSettingsRef.current.source === 'localStorage') {
          void putObservatorySettings({
            data: { settings: initialSettingsRef.current.settings },
          })
          return
        }
        const next = serverDefaults
        storeObservatorySettings(next)
        void putObservatorySettings({ data: { settings: next } })
        dispatchSettings({
          type: 'settings',
          stored: { settings: next, source: 'localStorage' },
        })
      })
      .catch(() => {
        if (!active) return
        dispatchSettings({ type: 'defaults', defaults: fallback })
      })
    return () => {
      active = false
    }
  }, [fallback])

  const value = useMemo<ObservatorySettingsContextValue>(
    () => ({
      settings,
      defaults,
      setSettings: (next) => {
        storeObservatorySettings(next)
        dispatchSettings({ type: 'replace', settings: next })
        void putObservatorySettings({ data: { settings: next } })
      },
      resetToDefaults: () => {
        storeObservatorySettings(defaults)
        dispatchSettings({ type: 'replace', settings: defaults })
        void putObservatorySettings({ data: { settings: defaults } })
      },
    }),
    [defaults, settings],
  )

  return (
    <ObservatorySettingsContext.Provider value={value}>
      {children}
    </ObservatorySettingsContext.Provider>
  )
}

export function useObservatorySettings(): ObservatorySettingsContextValue {
  const value = use(ObservatorySettingsContext)
  if (!value) {
    throw new Error(
      'useObservatorySettings must be used within ObservatorySettingsProvider',
    )
  }
  return value
}
