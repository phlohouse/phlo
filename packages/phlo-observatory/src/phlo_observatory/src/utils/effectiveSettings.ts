/**
 * Effective settings resolution for the browser: stored overrides beat
 * server defaults.
 */
import type { ObservatorySettings } from '@/lib/observatorySettings'
import { loadStoredObservatorySettings } from '@/lib/observatorySettings'
import { getObservatorySettingsDefaults } from '@/observatory/api/settings'

/**
 * Resolve the settings in force for this browser. Locally saved overrides win;
 * only a browser that has never stored settings falls back to the server
 * defaults.
 */
export async function getEffectiveObservatorySettings(): Promise<ObservatorySettings> {
  const stored = loadStoredObservatorySettings()
  if (stored.source === 'localStorage') return stored.settings
  return await getObservatorySettingsDefaults()
}
