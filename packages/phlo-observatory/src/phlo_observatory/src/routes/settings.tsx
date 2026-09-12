/**
 * /settings route. Local Observatory preferences, runtime settings,
 * capabilities, dataset workflow configuration, and cache statistics;
 * edits accumulate in a draft state until saved.
 */
import { createFileRoute } from '@tanstack/react-router'
import {
  Database,
  Gauge,
  KeyRound,
  Plug,
  RefreshCw,
  RotateCcw,
  Save,
  Settings,
  SlidersHorizontal,
} from 'lucide-react'
import { useEffect, useId, useMemo, useReducer, useState } from 'react'
import type { Dispatch, ReactNode } from 'react'

import type { ObservatorySettings } from '@/lib/observatorySettings'
import type {
  ObservatoryCapabilities,
  ObservatoryDatasetWorkflowConfig,
  ObservatoryResourceResult,
  ObservatoryRuntimeSettings,
} from '@/observatory/api/types'
import { useObservatoryExtensions } from '@/extensions/registry'
import { useObservatorySettings } from '@/hooks/useObservatorySettings'
import {
  getObservatoryCapabilities,
  getObservatoryDatasetWorkflowConfigDirect,
  getObservatoryRuntimeSettings,
  putObservatoryDatasetWorkflowConfigDirect,
} from '@/observatory/api/resources'
import {
  invalidateCachedResources,
  loadCachedResource,
} from '@/observatory/routes/liveResource'
import { labelValue } from '@/observatory/platformMetadata'
import { Page, PageHeader } from '@/components/observatory/page'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/settings')({
  component: SettingsRoute,
})

type CacheStats = {
  hits: number | null
  misses: number | null
  entries: number
  hitRate: number | null
  entriesByPrefix: Record<string, number>
}

type SettingsRouteState = {
  capabilities: ObservatoryResourceResult<ObservatoryCapabilities> | null
  draft: ObservatorySettings
  error: string | null
  stats: CacheStats | null
  statsLoading: boolean
}

type SettingsRouteAction =
  | { type: 'draft'; draft: ObservatorySettings }
  | {
      type: 'updateDraft'
      update: (draft: ObservatorySettings) => ObservatorySettings
    }
  | { type: 'error'; error: string | null }
  | { type: 'statsLoading'; loading: boolean }
  | { type: 'stats'; stats: CacheStats | null }
  | {
      type: 'capabilities'
      capabilities: ObservatoryResourceResult<ObservatoryCapabilities> | null
    }

function settingsRouteReducer(
  state: SettingsRouteState,
  action: SettingsRouteAction,
): SettingsRouteState {
  switch (action.type) {
    case 'draft':
      return { ...state, draft: action.draft }
    case 'updateDraft':
      return { ...state, draft: action.update(state.draft) }
    case 'error':
      return { ...state, error: action.error }
    case 'statsLoading':
      return { ...state, statsLoading: action.loading }
    case 'stats':
      return { ...state, stats: action.stats, statsLoading: false }
    case 'capabilities':
      return { ...state, capabilities: action.capabilities }
  }
}

function updateDraft(
  dispatch: Dispatch<SettingsRouteAction>,
  update: (draft: ObservatorySettings) => ObservatorySettings,
) {
  dispatch({ type: 'updateDraft', update })
}

export function SettingsRoute() {
  return useSettingsRoute()
}

function useSettingsRoute() {
  const { settings, setSettings, resetToDefaults } = useObservatorySettings()
  const { settingsSections } = useObservatoryExtensions()
  const [workflowConfig, setWorkflowConfig] =
    useState<ObservatoryDatasetWorkflowConfig | null>(null)
  const [workflowDraft, setWorkflowDraft] =
    useState<ObservatoryDatasetWorkflowConfig | null>(null)
  const [workflowMessage, setWorkflowMessage] = useState<string | null>(null)
  const [runtimeSettings, setRuntimeSettings] =
    useState<ObservatoryResourceResult<ObservatoryRuntimeSettings> | null>(null)
  const [{ capabilities, draft, error, stats, statsLoading }, dispatch] =
    useReducer(settingsRouteReducer, {
      capabilities: null,
      draft: settings,
      error: null,
      stats: null,
      statsLoading: false,
    })
  const orderedSettingsSections = useMemo(
    () =>
      settingsSections.slice().sort((a, b) => {
        const orderA = a.order ?? 0
        const orderB = b.order ?? 0
        if (orderA !== orderB) return orderA - orderB
        return a.title.localeCompare(b.title)
      }),
    [settingsSections],
  )
  const dirty = useMemo(
    () => JSON.stringify(draft) !== JSON.stringify(settings),
    [draft, settings],
  )
  const capabilityFeatures = capabilities?.data?.features ?? {}
  const saveHint = dirty ? 'Save browser preferences' : 'No changes to save'

  useEffect(() => {
    dispatch({ type: 'draft', draft: settings })
  }, [settings])

  useEffect(() => {
    void fetchStats()
    void getObservatoryDatasetWorkflowConfigDirect().then((next) => {
      if (next.data) {
        setWorkflowConfig(next.data)
        setWorkflowDraft(next.data)
      } else if (next.error) {
        setWorkflowMessage(next.error)
      }
    })
    void loadCachedResource(
      'observatory:capabilities',
      getObservatoryCapabilities,
      {
        force: true,
        staleMs: 30_000,
      },
    ).then((nextCapabilities) =>
      dispatch({ type: 'capabilities', capabilities: nextCapabilities }),
    )
    void loadCachedResource(
      'observatory:runtime-settings',
      getObservatoryRuntimeSettings,
      {
        force: true,
        staleMs: 30_000,
      },
    ).then(setRuntimeSettings)
  }, [])

  function fetchStats() {
    dispatch({ type: 'statsLoading', loading: true })
    try {
      dispatch({ type: 'stats', stats: readBrowserCacheStats() })
    } catch {
      dispatch({ type: 'stats', stats: null })
    } finally {
      dispatch({ type: 'statsLoading', loading: false })
    }
  }

  async function clearCache() {
    dispatch({ type: 'statsLoading', loading: true })
    try {
      clearBrowserCache()
      await fetchStats()
    } catch {
      dispatch({ type: 'statsLoading', loading: false })
    }
  }

  function save() {
    dispatch({ type: 'error', error: null })
    const validation = validateSettings(draft)
    if (validation) {
      dispatch({ type: 'error', error: validation })
      return
    }
    setSettings(draft)
  }

  function saveWorkflowConfig() {
    if (!workflowDraft) return
    const owner = workflowDraft.default_owner.trim()
    const approvalStates = workflowDraft.approval_states
      .map((state) => state.trim())
      .filter(Boolean)
    if (!owner || approvalStates.length === 0) {
      setWorkflowMessage('Default owner and approval states are required.')
      return
    }
    setWorkflowMessage('Saving workflow defaults...')
    void putObservatoryDatasetWorkflowConfigDirect({
      default_owner: owner,
      approval_states: approvalStates,
    }).then((next) => {
      if (next.data) {
        setWorkflowConfig(next.data)
        setWorkflowDraft(next.data)
        setWorkflowMessage('Workflow defaults saved.')
      } else {
        setWorkflowMessage(
          next.error ?? 'Workflow defaults could not be saved.',
        )
      }
    })
  }

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            <Settings className="size-3.5" />
            {dirty ? 'unsaved changes' : 'saved'}
          </Badge>
        }
        description="Runtime mode, provider coverage, cache state, workflow defaults, and local UI preferences."
        title="Platform settings"
      />
      <div className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto">
        <div className="bg-sheet border-rule flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 border">
          <div className="flex flex-col gap-0.5">
            <strong className="text-foreground text-sm font-semibold">
              Platform trust and preferences
            </strong>
            <span className="text-muted-foreground text-[11px]/relaxed">
              Runtime truth comes from phlo-api. Browser preferences only affect
              this local Observatory session.
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <Button
              onClick={() => {
                resetToDefaults()
                dispatch({ type: 'error', error: null })
              }}
              size="sm"
              type="button"
              variant="outline"
            >
              <RotateCcw className="size-3.5" />
              Reset
            </Button>
            <Button
              aria-label={saveHint}
              disabled={!dirty}
              onClick={save}
              size="sm"
              title={saveHint}
              type="button"
            >
              <Save className="size-3.5" />
              Save
            </Button>
          </div>
        </div>

        {!dirty && (
          <p className="text-muted-foreground font-mono text-[10px]">
            Settings are saved. Change a preference to enable Save.
          </p>
        )}

        {error && (
          <p className="border-status-error/40 bg-status-error/5 text-status-error border px-3 py-2 text-xs">
            {error}
          </p>
        )}

        <div className="grid grid-cols-2 items-start gap-3 max-xl:grid-cols-1">
          <SettingsPanel
            description="Live phlo-api contract for enabled surfaces, providers, defaults, and local cache state."
            icon={<Plug className="size-4" />}
            title="Runtime truth"
          >
            <RuntimeTruth
              runtimeSettings={runtimeSettings}
              stats={stats}
              statsLoading={statsLoading}
            />
          </SettingsPanel>

          <SettingsPanel
            description="Defaults used when opening table, query, and preview views."
            icon={<SlidersHorizontal className="size-4" />}
            title="Defaults"
          >
            <div className="grid grid-cols-3 gap-3 max-lg:grid-cols-1">
              {capabilityFeatures.branches && (
                <SettingField label="Branch">
                  <TextInput
                    value={draft.defaults.branch}
                    onChange={(value) =>
                      updateDraft(dispatch, (current) => ({
                        ...current,
                        defaults: { ...current.defaults, branch: value },
                      }))
                    }
                  />
                </SettingField>
              )}
              <SettingField label="Query default">
                <TextInput
                  value={draft.defaults.catalog}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      defaults: { ...current.defaults, catalog: value },
                    }))
                  }
                />
              </SettingField>
              <SettingField label="Schema">
                <TextInput
                  value={draft.defaults.schema}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      defaults: { ...current.defaults, schema: value },
                    }))
                  }
                />
              </SettingField>
            </div>
          </SettingsPanel>

          <SettingsPanel
            description="SQL execution limits and read-only protections."
            icon={<Database className="size-4" />}
            title="Query"
          >
            <div className="grid grid-cols-3 gap-3 max-lg:grid-cols-1">
              <SettingField label="Default LIMIT">
                <NumberInput
                  value={draft.query.defaultLimit}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      query: { ...current.query, defaultLimit: value },
                    }))
                  }
                />
              </SettingField>
              <SettingField label="Max LIMIT">
                <NumberInput
                  value={draft.query.maxLimit}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      query: { ...current.query, maxLimit: value },
                    }))
                  }
                />
              </SettingField>
              <SettingField label="Timeout (ms)">
                <NumberInput
                  value={draft.query.timeoutMs}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      query: { ...current.query, timeoutMs: value },
                    }))
                  }
                />
              </SettingField>
            </div>
            <ToggleRow
              checked={draft.query.readOnlyMode}
              description="Blocks non-read-only statements and enforces limits in SQL workflows."
              label="Read-only mode"
              onChange={(checked) =>
                updateDraft(dispatch, (current) => ({
                  ...current,
                  query: { ...current.query, readOnlyMode: checked },
                }))
              }
            />
          </SettingsPanel>

          <SettingsPanel
            description="Project defaults used by candidate and publication workflow actions."
            icon={<SlidersHorizontal className="size-4" />}
            title="Dataset workflow"
          >
            <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
              <SettingField label="Default owner">
                <TextInput
                  value={workflowDraft?.default_owner ?? ''}
                  onChange={(value) =>
                    setWorkflowDraft((current) => ({
                      default_owner: value,
                      approval_states: current?.approval_states ?? [
                        'draft',
                        'review',
                        'approved',
                        'rejected',
                        'retired',
                      ],
                    }))
                  }
                />
              </SettingField>
              <SettingField
                hint="Comma-separated states shown by publication workflows."
                label="Approval states"
              >
                <TextInput
                  value={workflowDraft?.approval_states.join(', ') ?? ''}
                  onChange={(value) =>
                    setWorkflowDraft((current) => ({
                      default_owner: current?.default_owner ?? '',
                      approval_states: value
                        .split(',')
                        .map((state) => state.trim()),
                    }))
                  }
                />
              </SettingField>
            </div>
            <div className="flex items-center gap-1.5">
              <Button
                disabled={
                  !workflowDraft ||
                  JSON.stringify(workflowDraft) ===
                    JSON.stringify(workflowConfig)
                }
                onClick={saveWorkflowConfig}
                size="sm"
                type="button"
              >
                <Save className="size-3.5" />
                Save workflow defaults
              </Button>
            </div>
            {workflowMessage && (
              <p className="text-muted-foreground font-mono text-[10px]">
                {workflowMessage}
              </p>
            )}
          </SettingsPanel>

          <SettingsPanel
            description="Display preferences shared by v1 and v2."
            icon={<Gauge className="size-4" />}
            title="Interface"
          >
            <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
              <SettingField label="Density">
                <SelectInput
                  options={[
                    ['comfortable', 'Comfortable'],
                    ['compact', 'Compact'],
                  ]}
                  value={draft.ui.density}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      ui: {
                        ...current.ui,
                        density: value as ObservatorySettings['ui']['density'],
                      },
                    }))
                  }
                />
              </SettingField>
              <SettingField label="Date format">
                <SelectInput
                  options={[
                    ['iso', 'ISO'],
                    ['local', 'Local'],
                  ]}
                  value={draft.ui.dateFormat}
                  onChange={(value) =>
                    updateDraft(dispatch, (current) => ({
                      ...current,
                      ui: {
                        ...current.ui,
                        dateFormat:
                          value as ObservatorySettings['ui']['dateFormat'],
                      },
                    }))
                  }
                />
              </SettingField>
            </div>
          </SettingsPanel>

          <SettingsPanel
            description="Authentication token and live update behavior for this browser session."
            icon={<KeyRound className="size-4" />}
            title="Access and updates"
          >
            <SettingField
              hint="Used when OBSERVATORY_AUTH_ENABLED=true."
              label="Auth token"
            >
              <TextInput
                placeholder="Enter auth token..."
                type="password"
                value={draft.auth?.token ?? ''}
                onChange={(value) =>
                  updateDraft(dispatch, (current) => ({
                    ...current,
                    auth: { ...current.auth, token: value || undefined },
                  }))
                }
              />
            </SettingField>
            <ToggleRow
              checked={draft.realtime?.enabled ?? true}
              description="Automatically poll dashboard and quality views for updates."
              label="Enable auto-refresh"
              onChange={(checked) =>
                updateDraft(dispatch, (current) => ({
                  ...current,
                  realtime: {
                    enabled: checked,
                    intervalMs: current.realtime?.intervalMs ?? 5000,
                  },
                }))
              }
            />
            <SettingField label="Polling interval (ms)">
              <NumberInput
                disabled={!(draft.realtime?.enabled ?? true)}
                max={60000}
                min={1000}
                step={1000}
                value={draft.realtime?.intervalMs ?? 5000}
                onChange={(value) =>
                  updateDraft(dispatch, (current) => ({
                    ...current,
                    realtime: {
                      enabled: current.realtime?.enabled ?? true,
                      intervalMs: value,
                    },
                  }))
                }
              />
            </SettingField>
          </SettingsPanel>

          {orderedSettingsSections.map((section) => {
            const SectionComponent = section.component
            return (
              <SettingsPanel
                description={
                  section.description ??
                  'Extension-provided settings registered with Observatory.'
                }
                icon={<Settings className="size-4" />}
                key={section.id}
                title={section.title}
              >
                <SectionComponent />
              </SettingsPanel>
            )
          })}

          <SettingsPanel
            description="Installed providers decide which Observatory surfaces appear in navigation."
            icon={<Plug className="size-4" />}
            title="Capabilities"
          >
            <div className="divide-border -mx-3 -mb-3 divide-y border-t">
              {(capabilities?.data?.pages ?? []).map((page) => (
                <MiniRow
                  detail={
                    page.available
                      ? page.providers.length
                        ? page.providers.map(labelValue).join(', ')
                        : 'core'
                      : (page.reason ?? 'No provider installed')
                  }
                  key={page.id}
                  label={page.label}
                />
              ))}
              {capabilities?.error && (
                <MiniRow
                  detail={capabilities.error}
                  label="Capability discovery"
                />
              )}
            </div>
          </SettingsPanel>

          <SettingsPanel
            description="Operator maintenance for Observatory read-model caches."
            icon={<RefreshCw className="size-4" />}
            title="Advanced"
          >
            <div className="flex items-center justify-between gap-2">
              <strong className="text-foreground text-[11px] font-medium">
                Metadata cache
              </strong>
              <div className="flex items-center gap-1.5">
                <Button
                  disabled={statsLoading}
                  onClick={() => void fetchStats()}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  <RefreshCw className="size-3.5" />
                  Refresh
                </Button>
                <Button
                  disabled={statsLoading}
                  onClick={() => void clearCache()}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  Clear cache
                </Button>
              </div>
            </div>
            <div className="border-border grid grid-cols-4 divide-x border-y max-lg:grid-cols-2">
              <CacheMetric
                label="Hits"
                value={
                  stats?.hits === null ? 'not tracked' : (stats?.hits ?? 0)
                }
              />
              <CacheMetric
                label="Misses"
                value={
                  stats?.misses === null ? 'not tracked' : (stats?.misses ?? 0)
                }
              />
              <CacheMetric
                label="Hit rate"
                value={
                  stats?.hitRate === null || stats?.hitRate === undefined
                    ? 'not tracked'
                    : `${(stats.hitRate * 100).toFixed(1)}%`
                }
              />
              <CacheMetric label="Entries" value={stats?.entries ?? 0} />
            </div>
            {stats?.entriesByPrefix &&
              Object.keys(stats.entriesByPrefix).length > 0 && (
                <div className="divide-border -mx-3 -mb-3 divide-y border-t">
                  {Object.entries(stats.entriesByPrefix).map(
                    ([prefix, count]) => (
                      <MiniRow
                        detail={String(count)}
                        key={prefix}
                        label={prefix}
                      />
                    ),
                  )}
                </div>
              )}
          </SettingsPanel>
        </div>
      </div>
    </Page>
  )
}

function MiniRow({ detail, label }: { detail: string; label: string }) {
  return (
    <div className="flex items-center justify-between gap-2 px-3 py-2">
      <span className="text-foreground min-w-0 text-[11px]">{label}</span>
      <span className="text-muted-foreground flex-none text-right font-mono text-[10px] break-all">
        {detail}
      </span>
    </div>
  )
}

function RuntimeTruth({
  runtimeSettings,
  stats,
  statsLoading,
}: {
  runtimeSettings: ObservatoryResourceResult<ObservatoryRuntimeSettings> | null
  stats: CacheStats | null
  statsLoading: boolean
}) {
  const settings = runtimeSettings?.data
  const features = settings?.features ?? {}
  const enabled = Object.entries(features).filter(([, value]) => value)
  const disabled = Object.entries(features).filter(([, value]) => !value)
  const providers = readProviders(settings)
  const defaults = settings?.defaults ?? {}
  const runtime = settings?.metadata.runtime

  return (
    <div className="flex flex-col gap-3">
      <div className="border-border grid grid-cols-4 divide-x border-y max-lg:grid-cols-2">
        <CacheMetric
          label="API contract"
          value={runtimeSettingsLabel(runtimeSettings)}
        />
        <CacheMetric label="Enabled surfaces" value={enabled.length} />
        <CacheMetric label="Disabled surfaces" value={disabled.length} />
        <CacheMetric
          label="Cache entries"
          value={statsLoading && !stats ? 'checking' : (stats?.entries ?? 0)}
        />
      </div>
      <div className="grid grid-cols-2 gap-3 max-lg:grid-cols-1">
        <div className="divide-border -my-1 divide-y">
          <MiniRow
            detail={
              Object.entries(defaults)
                .map(
                  ([key, value]) =>
                    `${labelize(key)}: ${
                      typeof value === 'string' ? labelValue(value) : value
                    }`,
                )
                .join(' · ') || 'No workflow defaults configured'
            }
            label="Defaults"
          />
          <MiniRow
            detail={
              settings
                ? formatSettingsStorage(settings.storage.settings)
                : runtimeSettings?.error
                  ? 'runtime settings unavailable'
                  : 'loading runtime settings'
            }
            label="Runtime context"
          />
          <MiniRow
            detail={runtime?.project_path || 'not reported'}
            label="Project path"
          />
          <MiniRow
            detail={runtime?.compose_project || 'not configured'}
            label="Compose project"
          />
          <MiniRow
            detail={runtime?.api_source || 'not reported'}
            label="API mode"
          />
          <MiniRow
            detail={
              disabled.map(([feature]) => labelize(feature)).join(', ') ||
              'none'
            }
            label="Disabled surfaces"
          />
          {runtimeSettings?.error && (
            <MiniRow
              detail={runtimeSettings.error}
              label="Runtime settings error"
            />
          )}
        </div>
        <div className="divide-border -my-1 divide-y">
          {providers.slice(0, 8).map(([surface, surfaceProviders]) => (
            <MiniRow
              detail={
                surfaceProviders.map(labelValue).join(', ') || 'No provider'
              }
              key={surface}
              label={labelize(surface)}
            />
          ))}
          {providers.length === 0 && (
            <MiniRow
              detail="No provider metadata available yet."
              label="Provider coverage"
            />
          )}
        </div>
      </div>
    </div>
  )
}

function runtimeSettingsLabel(
  runtimeSettings: ObservatoryResourceResult<ObservatoryRuntimeSettings> | null,
): string {
  if (runtimeSettings?.data) return `v${runtimeSettings.data.version}`
  if (runtimeSettings?.error) return 'unavailable'
  return 'checking'
}

function readProviders(
  settings: ObservatoryRuntimeSettings | null | undefined,
): Array<[string, Array<string>]> {
  const providers = settings?.metadata.providers
  if (!providers || typeof providers !== 'object') return []
  return Object.entries(providers)
    .map(
      ([surface, value]) =>
        [
          surface,
          Array.isArray(value)
            ? value.filter((item): item is string => typeof item === 'string')
            : [],
        ] as [string, Array<string>],
    )
    .sort(([left], [right]) => left.localeCompare(right))
}

function labelize(value: string): string {
  const label = value
    .replace(/[-_]/g, ' ')
    .replace(/\b\w/g, (letter) => letter.toUpperCase())
  return label.replace(/\bApis\b/g, 'APIs').replace(/\bBi\b/g, 'BI')
}

function formatSettingsStorage(value: string | undefined): string {
  if (value === 'core') return 'phlo-api core settings'
  if (!value) return 'not reported'
  return labelize(value)
}

const browserCacheVersion = '2026-07-10-observatory-runtime-v11'
const browserCachePrefix = `phlo-observatory:${browserCacheVersion}:`
const browserResourceKeys = [
  'observatory:apis',
  'observatory:bi',
  'observatory:branches',
  'observatory:capabilities',
  'observatory:datasets',
  'observatory:extensions',
  'observatory:governance',
  'observatory:governance-matrix',
  'observatory:logs',
  'observatory:observability',
  'observatory:operations',
  'observatory:overview',
  'observatory:pipelines',
  'observatory:quality',
  'observatory:runs',
  'observatory:runtime-settings',
  'observatory:services',
  'observatory:storage',
  'observatory:tables',
  'observatory:workflow-wizard',
]

function readBrowserCacheStats(): CacheStats {
  const storage = browserSessionStorage()
  if (!storage) {
    return {
      entries: 0,
      entriesByPrefix: {},
      hitRate: null,
      hits: null,
      misses: null,
    }
  }

  const entriesByPrefix: Record<string, number> = {}
  let entries = 0
  const expiredKeys: Array<string> = []
  for (let index = 0; index < storage.length; index += 1) {
    const storageKey = storage.key(index)
    if (!storageKey?.startsWith(browserCachePrefix)) continue
    if (isExpiredBrowserCacheEntry(storage.getItem(storageKey))) {
      expiredKeys.push(storageKey)
      continue
    }
    entries += 1
    const resourceKey = storageKey.slice(browserCachePrefix.length)
    const prefix = resourceKey.split(':').slice(0, 2).join(':') || resourceKey
    entriesByPrefix[prefix] = (entriesByPrefix[prefix] ?? 0) + 1
  }

  for (const key of expiredKeys) storage.removeItem(key)

  return {
    entries,
    entriesByPrefix,
    hitRate: null,
    hits: null,
    misses: null,
  }
}

function clearBrowserCache(): void {
  invalidateCachedResources(browserResourceKeys)
  const storage = browserSessionStorage()
  if (!storage) return
  const keys: Array<string> = []
  for (let index = 0; index < storage.length; index += 1) {
    const storageKey = storage.key(index)
    if (storageKey?.startsWith(browserCachePrefix)) keys.push(storageKey)
  }
  for (const key of keys) storage.removeItem(key)
}

function browserSessionStorage(): Storage | null {
  if (typeof window === 'undefined') return null
  try {
    return window.sessionStorage
  } catch {
    return null
  }
}

function isExpiredBrowserCacheEntry(raw: string | null): boolean {
  if (!raw) return true
  try {
    const parsed = JSON.parse(raw) as { expiresAt?: unknown }
    return (
      typeof parsed.expiresAt !== 'number' || parsed.expiresAt <= Date.now()
    )
  } catch {
    return true
  }
}

function SettingsPanel({
  children,
  description,
  icon,
  title,
}: {
  children: ReactNode
  description: string
  icon: ReactNode
  title: string
}) {
  return (
    <SectionCard
      contentClassName="flex flex-col gap-3 p-3"
      description={description}
      title={
        <span className="flex items-center gap-1.5">
          {icon}
          {title}
        </span>
      }
    >
      {children}
    </SectionCard>
  )
}

function SettingField({
  children,
  hint,
  label,
}: {
  children: ReactNode
  hint?: string
  label: string
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted-foreground text-[11px] font-medium">
        {label}
      </span>
      {children}
      {hint && (
        <small className="text-muted-foreground text-[10px]">{hint}</small>
      )}
    </label>
  )
}

function TextInput({
  onChange,
  type = 'text',
  ...props
}: {
  disabled?: boolean
  onChange: (value: string) => void
  placeholder?: string
  type?: string
  value: string
}) {
  return (
    <Input
      {...props}
      type={type}
      onChange={(event) => onChange(event.target.value)}
    />
  )
}

function NumberInput({
  disabled,
  max,
  min,
  onChange,
  step,
  value,
}: {
  disabled?: boolean
  max?: number
  min?: number
  onChange: (value: number) => void
  step?: number
  value: number
}) {
  return (
    <Input
      disabled={disabled}
      max={max}
      min={min}
      step={step}
      type="number"
      value={value}
      onChange={(event) => onChange(Number(event.target.value) || 0)}
    />
  )
}

function SelectInput({
  onChange,
  options,
  value,
}: {
  onChange: (value: string) => void
  options: Array<[string, string]>
  value: string
}) {
  return (
    <Select
      onValueChange={(next) => {
        if (typeof next === 'string') onChange(next)
      }}
      value={value}
    >
      <SelectTrigger>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        {options.map(([optionValue, label]) => (
          <SelectItem key={optionValue} value={optionValue}>
            {label}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}

function ToggleRow({
  checked,
  description,
  label,
  onChange,
}: {
  checked: boolean
  description: string
  label: string
  onChange: (checked: boolean) => void
}) {
  const inputId = useId()
  return (
    <label
      className="border-border flex items-center gap-2.5 border px-3 py-2"
      htmlFor={inputId}
    >
      <Switch
        id={inputId}
        aria-label={label}
        checked={checked}
        onCheckedChange={(next) => onChange(next)}
      />
      <span className="flex min-w-0 flex-col gap-0.5">
        <strong className="text-foreground text-[11px] font-medium">
          {label}
        </strong>
        <small className="text-muted-foreground text-[10px]/relaxed">
          {description}
        </small>
      </span>
    </label>
  )
}

function CacheMetric({
  label,
  value,
}: {
  label: string
  value: number | string
}) {
  return (
    <div className={cn('flex flex-col gap-0.5 px-3 py-2')}>
      <span className="text-muted-foreground font-mono text-[9px] font-medium tracking-widest uppercase">
        {label}
      </span>
      <strong className="text-foreground truncate font-mono text-[11px]">
        {value}
      </strong>
    </div>
  )
}

function validateSettings(settings: ObservatorySettings): string | null {
  if (settings.query.defaultLimit > settings.query.maxLimit) {
    return 'Default LIMIT must be less than or equal to Max LIMIT.'
  }
  return null
}
