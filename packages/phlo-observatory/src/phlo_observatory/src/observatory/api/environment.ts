/** Explicit, per-browser environment selection for v1 API requests. */
export type ObservatoryEnvironment = 'prod' | 'staging'

const storageKey = 'phlo-observatory:selected-environment'
const changeEvent = 'phlo-observatory:environment-change'
let selected: ObservatoryEnvironment | null = null

export function selectedEnvironment(): ObservatoryEnvironment | null {
  if (typeof window === 'undefined') return null
  const value = window.localStorage?.getItem(storageKey)
  if (value === 'prod' || value === 'staging') return value
  return selected
}

export function selectEnvironment(environment: ObservatoryEnvironment): void {
  selected = environment
  window.localStorage?.setItem(storageKey, environment)
  window.dispatchEvent(new Event(changeEvent))
}

export function environmentChangeEvent(): string {
  return changeEvent
}

export function v1Endpoint(
  path: string,
  environment = selectedEnvironment(),
): string {
  if (!environment) {
    throw new Error(
      'Select prod or staging before loading environment-scoped data.',
    )
  }
  const separator = path.includes('?') ? '&' : '?'
  return `${path}${separator}env=${encodeURIComponent(environment)}`
}
