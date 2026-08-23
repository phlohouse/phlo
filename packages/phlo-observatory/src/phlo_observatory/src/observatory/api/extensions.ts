/**
 * Fetches the Observatory extension manifest from phlo-api and shapes it into
 * contributed nav items, routes, slots, and typed settings access.
 */
import { createServerFn } from '@tanstack/react-start'

import { apiGet } from '@/server/phlo-api'

export type ObservatoryExtensionRoute = {
  path: string
  module: string
  export: string
}

export type ObservatoryExtensionNavItem = {
  title: string
  to: string
}

type ObservatoryExtensionSlot = {
  slot_id: string
  module: string
  export: string
}

type ObservatoryExtensionSettings = {
  module: string
  export: string
}

export type ObservatoryExtensionManifest = {
  name: string
  version: string
  compat: {
    observatory_min: string
  }
  settings?: {
    settings_schema: Record<string, {}>
    defaults?: Record<string, {}>
    scope?: 'global' | 'extension'
  }
  ui?: {
    routes?: Array<ObservatoryExtensionRoute>
    nav?: Array<ObservatoryExtensionNavItem>
    slots?: Array<ObservatoryExtensionSlot>
    settings?: Array<ObservatoryExtensionSettings>
  }
}

type ObservatoryExtensionDescriptor = {
  manifest: ObservatoryExtensionManifest
  assets_base_path: string
}

export type ObservatoryExtensionResponse = {
  extensions: Array<ObservatoryExtensionDescriptor>
}

const PHLO_API_URL = process.env.PHLO_API_URL || 'http://localhost:4000'

// Manifest module paths are relative to the extension's assets directory;
// rewrite them to absolute URLs so the browser can fetch remote extension
// modules. Absolute http(s) paths pass through untouched.
function withAssetUrl(basePath: string, path: string): string {
  if (path.startsWith('http://') || path.startsWith('https://')) return path
  const normalized = path.startsWith('/') ? path : `/${path}`
  return `${PHLO_API_URL}${basePath}${normalized}`
}

export type ObservatoryExtension = {
  manifest: ObservatoryExtensionManifest
  assetsBasePath: string
  assetsBaseUrl: string
}

export async function resolveObservatoryExtensions(
  fetchExtensions: () => Promise<ObservatoryExtensionResponse> = () =>
    apiGet<ObservatoryExtensionResponse>(
      '/api/observatory/extension-manifests',
    ),
): Promise<Array<ObservatoryExtension>> {
  let response: ObservatoryExtensionResponse
  try {
    response = await fetchExtensions()
  } catch {
    // Extensions are optional decoration: an unreachable phlo-api yields an
    // empty list rather than failing the whole page render.
    return []
  }

  return response.extensions.map((entry) => {
    const basePath = entry.assets_base_path
    const assetsBaseUrl = `${PHLO_API_URL}${basePath}`

    const manifest: ObservatoryExtensionManifest = {
      ...entry.manifest,
      ui: entry.manifest.ui
        ? {
            ...entry.manifest.ui,
            routes: entry.manifest.ui.routes?.map((route) => ({
              ...route,
              module: withAssetUrl(basePath, route.module),
            })),
            slots: entry.manifest.ui.slots?.map((slot) => ({
              ...slot,
              module: withAssetUrl(basePath, slot.module),
            })),
            settings: entry.manifest.ui.settings?.map((setting) => ({
              ...setting,
              module: withAssetUrl(basePath, setting.module),
            })),
          }
        : undefined,
    }

    return {
      manifest,
      assetsBasePath: basePath,
      assetsBaseUrl,
    }
  })
}

export const getObservatoryExtensions = createServerFn().handler(() =>
  resolveObservatoryExtensions(),
)
