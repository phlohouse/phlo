/**
 * Extension registry for the Observatory UI.
 *
 * Loads extension manifests (bundled at build time or served by phlo-api)
 * and lets each module register routes, slot components, and settings
 * sections through a shared React context.
 */
import {
  createContext,
  use,
  useEffect,
  useMemo,
  useReducer,
  useRef,
} from 'react'
import { createRoute, useRouter } from '@tanstack/react-router'
import type { ComponentType, ReactNode } from 'react'
import type { AnyRoute } from '@tanstack/react-router'

import type {
  ObservatoryExtension,
  ObservatoryExtensionNavItem,
  ObservatoryExtensionRoute,
} from '@/observatory/api/extensions'
import { getObservatoryExtensions } from '@/observatory/api/extensions'
import {
  getExtensionSettings,
  putExtensionSettings,
} from '@/observatory/api/extension-settings'

type ExtensionRouteContext = {
  createRoute: typeof createRoute
  rootRoute: AnyRoute
  extensionName: string
  route: ObservatoryExtensionRoute
}

type RegisterRoutesFn = (
  ctx: ExtensionRouteContext,
) => AnyRoute | Array<AnyRoute> | void

export type SlotRegistry = {
  register: (component: ComponentType) => void
}

type RegisterSlotFn = (registry: SlotRegistry) => void

type ExtensionSettingsSection = {
  id: string
  title: string
  description?: string
  order?: number
  component: ComponentType
}

export type SettingsRegistry = {
  register: (section: ExtensionSettingsSection) => void
  loadSettings: () => Promise<Record<string, unknown>>
  saveSettings: (settings: Record<string, unknown>) => Promise<void>
  scope: 'global' | 'extension'
}

type RegisterSettingsFn = (registry: SettingsRegistry) => void
type ExtensionModule = Record<string, unknown>
type ExtensionModuleLoader = () => Promise<ExtensionModule>

type ExtensionRegistryState = {
  extensions: Array<ObservatoryExtension>
  navItems: Array<ObservatoryExtensionNavItem>
  slots: Record<string, Array<ComponentType>>
  settingsSections: Array<ExtensionSettingsSection>
}

type ExtensionRegistryAction =
  | {
      type: 'loaded'
      extensions: Array<ObservatoryExtension>
      navItems: Array<ObservatoryExtensionNavItem>
    }
  | { type: 'slot'; slotId: string; component: ComponentType }
  | { type: 'settings'; section: ExtensionSettingsSection }

function extensionRegistryReducer(
  state: ExtensionRegistryState,
  action: ExtensionRegistryAction,
): ExtensionRegistryState {
  switch (action.type) {
    case 'loaded':
      return {
        ...state,
        extensions: action.extensions,
        navItems: action.navItems,
      }
    case 'slot':
      return {
        ...state,
        slots: {
          ...state.slots,
          [action.slotId]: [
            ...(state.slots[action.slotId] ?? []),
            action.component,
          ],
        },
      }
    case 'settings': {
      const settingsSections = state.settingsSections.filter(
        (item) => item.id !== action.section.id,
      )
      return {
        ...state,
        settingsSections: [...settingsSections, action.section],
      }
    }
  }
}

const ExtensionRegistryContext = createContext<ExtensionRegistryState | null>(
  null,
)

function uniqueNavItems(items: Array<ObservatoryExtensionNavItem>) {
  const seen = new Set<string>()
  return items.filter((item) => {
    const key = `${item.title}:${item.to}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

const bundledExtensionModules = import.meta.glob<ExtensionModule>(
  '/src/extensions/**/*.{ts,tsx,js,jsx}',
)

function browserApiBase(): string {
  if (typeof window === 'undefined') return ''
  return (
    window.__PHLO_API_BROWSER_URL__ ??
    document.querySelector<HTMLMetaElement>('meta[name="phlo-api-browser-url"]')
      ?.content ??
    ''
  ).trim()
}

function withBrowserAssetUrl(baseUrl: string, basePath: string, path: string) {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${baseUrl}${basePath}${normalized}`
}

async function loadBrowserExtensions(): Promise<Array<ObservatoryExtension>> {
  const baseUrl = browserApiBase()
  if (!baseUrl) return getObservatoryExtensions()

  const response = await fetch(`${baseUrl}/api/observatory/extension-manifests`)
  if (!response.ok) {
    throw new Error(
      `phlo-api extension manifest error: ${response.status} ${response.statusText}`,
    )
  }
  const payload = (await response.json()) as {
    extensions: Array<{
      manifest: ObservatoryExtension['manifest']
      assets_base_path: string
    }>
  }

  return payload.extensions.map((entry) => {
    const basePath = entry.assets_base_path
    const assetsBaseUrl = `${baseUrl}${basePath}`
    const manifest = entry.manifest
    return {
      manifest: {
        ...manifest,
        ui: manifest.ui
          ? {
              ...manifest.ui,
              routes: manifest.ui.routes?.map((route) => ({
                ...route,
                module: withBrowserAssetUrl(baseUrl, basePath, route.module),
              })),
              slots: manifest.ui.slots?.map((slot) => ({
                ...slot,
                module: withBrowserAssetUrl(baseUrl, basePath, slot.module),
              })),
              settings: manifest.ui.settings?.map((setting) => ({
                ...setting,
                module: withBrowserAssetUrl(baseUrl, basePath, setting.module),
              })),
            }
          : undefined,
      },
      assetsBasePath: basePath,
      assetsBaseUrl,
    }
  })
}

function loadExtensionModule(moduleUrl: string): Promise<ExtensionModule> {
  const loader = bundledExtensionModules[moduleUrl] as
    | ExtensionModuleLoader
    | undefined
  if (loader) return loader()
  return Promise.resolve({})
}

export function ObservatoryExtensionProvider({
  children,
}: {
  children: ReactNode
}) {
  const router = useRouter()
  const [state, dispatch] = useReducer(extensionRegistryReducer, {
    extensions: [],
    navItems: [],
    slots: {},
    settingsSections: [],
  })
  const registeredExtensions = useRef(new Set<string>())

  useEffect(() => {
    if (typeof window === 'undefined') return

    let active = true

    const registerSlot = (slotId: string): SlotRegistry['register'] => {
      return (component) => {
        dispatch({ type: 'slot', slotId, component })
      }
    }

    const registerSettings: SettingsRegistry['register'] = (section) => {
      dispatch({ type: 'settings', section })
    }

    const loadExtensions = async () => {
      if (import.meta.env.DEV && !browserApiBase()) {
        return
      }

      let entries: Array<ObservatoryExtension>
      try {
        entries = await loadBrowserExtensions()
      } catch (error) {
        console.error(
          'Failed to load Observatory extensions via getObservatoryExtensions',
          error,
        )
        if (process.env.NODE_ENV !== 'production') {
          console.warn(
            'Observatory extensions failed to load; check phlo-api and extension manifests.',
          )
        }
        return
      }
      if (!active) return

      const nextNavItems = uniqueNavItems(
        entries.flatMap((entry) => entry.manifest.ui?.nav ?? []),
      )
      dispatch({ type: 'loaded', extensions: entries, navItems: nextNavItems })

      const rootRoute = router.options.routeTree as AnyRoute | undefined
      const nextRoutes: Array<AnyRoute> = []

      if (!rootRoute) return

      const registerExtension = async (extension: ObservatoryExtension) => {
        const extensionName = extension.manifest.name
        if (registeredExtensions.current.has(extensionName)) {
          return
        }
        registeredExtensions.current.add(extensionName)

        const routeTasks: Array<Promise<void>> = []
        for (const route of extension.manifest.ui?.routes ?? []) {
          if (route.path.startsWith('/extensions/')) continue
          routeTasks.push(
            (async () => {
              try {
                const module = await loadExtensionModule(route.module)
                const registerRoutes = module[route.export] as
                  | RegisterRoutesFn
                  | undefined
                if (typeof registerRoutes !== 'function') return
                const result = registerRoutes({
                  createRoute,
                  rootRoute,
                  extensionName,
                  route,
                })
                if (Array.isArray(result)) {
                  nextRoutes.push(...result)
                } else if (result) {
                  nextRoutes.push(result)
                }
              } catch (error) {
                console.debug('Failed to register extension routes', {
                  extensionName,
                  module: route.module,
                  export: route.export,
                  error,
                })
              }
            })(),
          )
        }

        const slotTasks: Array<Promise<void>> = []
        for (const slot of extension.manifest.ui?.slots ?? []) {
          slotTasks.push(
            (async () => {
              try {
                const module = await loadExtensionModule(slot.module)
                const registerSlotFn = module[slot.export] as
                  | RegisterSlotFn
                  | undefined
                if (typeof registerSlotFn !== 'function') return
                registerSlotFn({ register: registerSlot(slot.slot_id) })
              } catch {
                // Ignore optional extension slots that cannot be loaded.
              }
            })(),
          )
        }

        const settingTasks: Array<Promise<void>> = []
        for (const setting of extension.manifest.ui?.settings ?? []) {
          settingTasks.push(
            (async () => {
              try {
                const module = await loadExtensionModule(setting.module)
                const registerSettingsFn = module[setting.export] as
                  | RegisterSettingsFn
                  | undefined
                if (typeof registerSettingsFn !== 'function') return
                const scope = extension.manifest.settings?.scope ?? 'extension'
                const loadSettings = async () => {
                  const response = await getExtensionSettings({
                    data: { name: extensionName },
                  })
                  return response.settings ?? {}
                }
                const saveSettings = async (
                  settings: Record<string, unknown>,
                ) => {
                  await putExtensionSettings({
                    data: { name: extensionName, settings },
                  })
                }
                registerSettingsFn({
                  register: registerSettings,
                  loadSettings,
                  saveSettings,
                  scope,
                })
              } catch {
                // Ignore optional extension settings that cannot be loaded.
              }
            })(),
          )
        }

        await Promise.all([...routeTasks, ...slotTasks, ...settingTasks])
      }

      await Promise.all(entries.map(registerExtension))

      if (nextRoutes.length) {
        const nextRouteTree = rootRoute.addChildren(nextRoutes)
        router.update({
          ...router.options,
          routeTree:
            nextRouteTree as unknown as typeof router.options.routeTree,
        })
      }
    }

    void loadExtensions()

    return () => {
      active = false
    }
  }, [router])

  const value = useMemo<ExtensionRegistryState>(() => state, [state])

  return (
    <ExtensionRegistryContext.Provider value={value}>
      {children}
    </ExtensionRegistryContext.Provider>
  )
}

export function useObservatoryExtensions() {
  const value = use(ExtensionRegistryContext)
  if (!value) {
    throw new Error(
      'useObservatoryExtensions must be used within ObservatoryExtensionProvider',
    )
  }
  return value
}

export function ExtensionSlot({
  slotId,
  className,
}: {
  slotId: string
  className?: string
}) {
  const { slots } = useObservatoryExtensions()
  const components = slots[slotId] ?? []

  if (!components.length) return null

  return (
    <div className={className}>
      {components.map((Component) => (
        <Component
          key={`${slotId}-${Component.displayName ?? Component.name}`}
        />
      ))}
    </div>
  )
}
