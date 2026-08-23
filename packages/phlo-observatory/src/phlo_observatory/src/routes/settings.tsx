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
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import {
  invalidateCachedResources,
  loadCachedResource,
} from '@/observatory/routes/liveResource'
import { labelValue } from '@/observatory/platformMetadata'

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

  async function fetchStats() {
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
    <ObservatoryPage
      kicker="Settings"
      title="Platform settings"
      description="Runtime mode, provider coverage, cache state, workflow defaults, and local UI preferences."
      action={
        <span className="phlo-observatory-pill">
          <Settings className="size-3.5" />
          {dirty ? 'unsaved changes' : 'saved'}
        </span>
      }
    >
      <section className="phlo-observatory-settings-workbench">
        <div className="phlo-observatory-settings-toolbar">
          <div>
            <strong>Platform trust and preferences</strong>
            <span>
              Runtime truth comes from phlo-api. Browser preferences only affect
              this local Observatory session.
            </span>
          </div>
          <div className="phlo-observatory-action-row">
            <button
              onClick={() => {
                resetToDefaults()
                dispatch({ type: 'error', error: null })
              }}
              type="button"
            >
              <RotateCcw className="size-3.5" />
              Reset
            </button>
            <button
              aria-label={saveHint}
              disabled={!dirty}
              onClick={save}
              title={saveHint}
              type="button"
            >
              <Save className="size-3.5" />
              Save
            </button>
          </div>
        </div>

        {!dirty && (
          <div className="phlo-observatory-panel-footer">
            Settings are saved. Change a preference to enable Save.
          </div>
        )}

        {error && (
          <div className="phlo-observatory-settings-error">{error}</div>
        )}

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
          <div className="phlo-observatory-settings-columns">
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
          <div className="phlo-observatory-settings-columns">
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
          <div className="phlo-observatory-settings-columns">
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
          <div className="phlo-observatory-action-row">
            <button
              disabled={
                !workflowDraft ||
                JSON.stringify(workflowDraft) === JSON.stringify(workflowConfig)
              }
              onClick={saveWorkflowConfig}
              type="button"
            >
              <Save className="size-3.5" />
              Save workflow defaults
            </button>
          </div>
          {workflowMessage && (
            <div className="phlo-observatory-panel-footer">
              {workflowMessage}
            </div>
          )}
        </SettingsPanel>

        <SettingsPanel
          description="Display preferences shared by v1 and v2."
          icon={<Gauge className="size-4" />}
          title="Interface"
        >
          <div className="phlo-observatory-settings-columns">
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
          <div className="phlo-observatory-detail-list">
            {(capabilities?.data?.pages ?? []).map((page) => (
              <div className="phlo-observatory-mini-row" key={page.id}>
                <span>{page.label}</span>
                <small>
                  {page.available
                    ? page.providers.length
                      ? page.providers.map(labelValue).join(', ')
                      : 'core'
                    : (page.reason ?? 'No provider installed')}
                </small>
              </div>
            ))}
            {capabilities?.error && (
              <div className="phlo-observatory-mini-row">
                <span>Capability discovery</span>
                <small>{capabilities.error}</small>
              </div>
            )}
          </div>
        </SettingsPanel>

        <SettingsPanel
          description="Operator maintenance for Observatory read-model caches."
          icon={<RefreshCw className="size-4" />}
          title="Advanced"
        >
          <div className="phlo-observatory-cache-header">
            <strong>Metadata cache</strong>
            <div className="phlo-observatory-action-row">
              <button
                disabled={statsLoading}
                onClick={() => void fetchStats()}
                type="button"
              >
                <RefreshCw className="size-3.5" />
                Refresh
              </button>
              <button
                disabled={statsLoading}
                onClick={() => void clearCache()}
                type="button"
              >
                Clear cache
              </button>
            </div>
          </div>
          <div className="phlo-observatory-cache-grid">
            <CacheMetric
              label="Hits"
              value={stats?.hits === null ? 'not tracked' : (stats?.hits ?? 0)}
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
              <div className="phlo-observatory-detail-list">
                {Object.entries(stats.entriesByPrefix).map(
                  ([prefix, count]) => (
                    <div className="phlo-observatory-mini-row" key={prefix}>
                      <span>{prefix}</span>
                      <small>{count}</small>
                    </div>
                  ),
                )}
              </div>
            )}
        </SettingsPanel>
      </section>
    </ObservatoryPage>
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
    <div className="phlo-observatory-runtime-truth">
      <div className="phlo-observatory-cache-grid">
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
      <div className="phlo-observatory-runtime-columns">
        <div className="phlo-observatory-detail-list">
          <div className="phlo-observatory-mini-row">
            <span>Defaults</span>
            <small>
              {Object.entries(defaults)
                .map(
                  ([key, value]) =>
                    `${labelize(key)}: ${
                      typeof value === 'string' ? labelValue(value) : value
                    }`,
                )
                .join(' · ') || 'No workflow defaults configured'}
            </small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Runtime context</span>
            <small>
              {settings
                ? formatSettingsStorage(settings.storage.settings)
                : runtimeSettings?.error
                  ? 'runtime settings unavailable'
                  : 'loading runtime settings'}
            </small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Project path</span>
            <small>{runtime?.project_path || 'not reported'}</small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Compose project</span>
            <small>{runtime?.compose_project || 'not configured'}</small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>API mode</span>
            <small>{runtime?.api_source || 'not reported'}</small>
          </div>
          <div className="phlo-observatory-mini-row">
            <span>Disabled surfaces</span>
            <small>
              {disabled.map(([feature]) => labelize(feature)).join(', ') ||
                'none'}
            </small>
          </div>
          {runtimeSettings?.error && (
            <div className="phlo-observatory-mini-row">
              <span>Runtime settings error</span>
              <small>{runtimeSettings.error}</small>
            </div>
          )}
        </div>
        <div className="phlo-observatory-detail-list">
          {providers.slice(0, 8).map(([surface, surfaceProviders]) => (
            <div className="phlo-observatory-mini-row" key={surface}>
              <span>{labelize(surface)}</span>
              <small>
                {surfaceProviders.map(labelValue).join(', ') || 'No provider'}
              </small>
            </div>
          ))}
          {providers.length === 0 && (
            <div className="phlo-observatory-mini-row">
              <span>Provider coverage</span>
              <small>No provider metadata available yet.</small>
            </div>
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
    <section className="phlo-observatory-settings-panel">
      <div className="phlo-observatory-callout-title">
        {icon}
        {title}
      </div>
      <p>{description}</p>
      <div className="phlo-observatory-settings-panel-body">{children}</div>
    </section>
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
    <label className="phlo-observatory-settings-field">
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
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
    <input
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
    <input
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
    <select value={value} onChange={(event) => onChange(event.target.value)}>
      {options.map(([optionValue, label]) => (
        <option key={optionValue} value={optionValue}>
          {label}
        </option>
      ))}
    </select>
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
    <label className="phlo-observatory-toggle-row" htmlFor={inputId}>
      <input
        id={inputId}
        aria-label={label}
        checked={checked}
        type="checkbox"
        onChange={(event) => onChange(event.target.checked)}
      />
      <span>
        <strong>{label}</strong>
        <small>{description}</small>
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
    <div className="phlo-observatory-cache-metric">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  )
}

function validateSettings(settings: ObservatorySettings): string | null {
  if (settings.query.defaultLimit > settings.query.maxLimit) {
    return 'Default LIMIT must be less than or equal to Max LIMIT.'
  }
  return null
}
