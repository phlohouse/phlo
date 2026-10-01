/**
 * /workspace route. Environment-scoped v1 inventory for supported resources.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Boxes, Database, FileCode2, GitBranch, Workflow } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

import { getBranchReferences } from '@/observatory/api/branches'
import {
  environmentChangeEvent,
  selectedEnvironment,
} from '@/observatory/api/environment'
import { getV1PipelineSnapshot } from '@/observatory/api/pipelinesV1'
import { getV1AssetsPage } from '@/observatory/api/tablesV1'
import { getSavedQueries } from '@/observatory/api/trino'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/workspace')({ component: Workspace })

type WorkspaceLoad<T> = { data: Array<T> | null; error: string | null }
const environmentRequired = {
  data: null,
  error: 'Select prod or staging before loading environment-scoped inventory.',
}

function knownCount<T>(
  result: WorkspaceLoad<T>,
  count: (data: Array<T>) => number = (data) => data.length,
): number | null | undefined {
  if (result.error) return null
  return result.data ? count(result.data) : undefined
}

export function Workspace() {
  const [environment, setEnvironment] = useState(selectedEnvironment)

  useEffect(() => {
    const update = () => setEnvironment(selectedEnvironment())
    window.addEventListener(environmentChangeEvent(), update)
    return () => window.removeEventListener(environmentChangeEvent(), update)
  }, [])

  const loadTables = useCallback(
    () =>
      environment
        ? getV1AssetsPage({ data: { environment, cursor: null } }).then(
            (result) =>
              result.kind === 'available'
                ? result.data.next_cursor === null
                  ? { data: result.data.items, error: null }
                  : {
                      data: null,
                      error:
                        'Table inventory spans multiple pages; the total count is unavailable.',
                    }
                : { data: null, error: result.message },
          )
        : Promise.resolve(environmentRequired),
    [environment],
  )
  const tables = useLiveResource(
    loadTables,
    60_000,
    `observatory:workspace:tables:${environment ?? 'unselected'}`,
  )
  const loadPipelines = useCallback(
    () =>
      environment
        ? getV1PipelineSnapshot({ data: { environment } }).then((result) => ({
            data: result.data === null ? null : [result.data],
            error: result.error,
          }))
        : Promise.resolve(environmentRequired),
    [environment],
  )
  const pipelines = useLiveResource(
    loadPipelines,
    60_000,
    `observatory:workspace:pipelines:${environment ?? 'unselected'}`,
  )
  const loadBranches = useCallback(
    () =>
      environment
        ? getBranchReferences({ data: { env: environment } }).then((result) =>
            result.kind === 'available'
              ? { data: result.data, error: null }
              : { data: null, error: result.message },
          )
        : Promise.resolve(environmentRequired),
    [environment],
  )
  const branches = useLiveResource(
    loadBranches,
    60_000,
    `observatory:workspace:branches:${environment ?? 'unselected'}`,
  )
  const loadQueries = useCallback(
    () =>
      environment
        ? getSavedQueries({ data: { environment } })
            .then((data) =>
              data.every((query) => query.env === environment)
                ? { data, error: null }
                : {
                    data: null,
                    error:
                      'phlo-api returned saved queries for another environment.',
                  },
            )
            .catch((error: unknown) => ({
              data: null,
              error:
                error instanceof Error
                  ? error.message
                  : 'Saved queries are unavailable.',
            }))
        : Promise.resolve(environmentRequired),
    [environment],
  )
  const queries = useLiveResource(
    loadQueries,
    30_000,
    `observatory:workspace:saved-queries:${environment ?? 'unselected'}`,
  )
  const loading = [tables, pipelines, branches, queries].some(
    (item) => item.isLoading,
  )

  const resources = [
    {
      label: 'Datasets',
      detail: 'Dataset contract unavailable.',
      count: null,
      href: '/datasets',
      icon: Boxes,
    },
    {
      label: 'Tables',
      detail: tables.error ?? 'Queryable physical inventory',
      count: knownCount(tables),
      href: '/tables',
      icon: Database,
    },
    {
      label: 'Pipelines',
      detail: pipelines.error ?? 'Jobs, schedules, and recent run evidence',
      count: knownCount(
        pipelines,
        (snapshots) => snapshots[0]?.jobs.items.length ?? 0,
      ),
      href: '/pipelines',
      icon: Workflow,
    },
    {
      label: 'Saved queries',
      detail: queries.error ?? 'Read-only SQL workspace objects',
      count: knownCount(queries),
      href: '/queries',
      icon: FileCode2,
    },
    {
      label: 'Change reviews',
      detail: branches.error ?? 'Branches and proposed lakehouse changes',
      count: knownCount(
        branches,
        (references) =>
          references.filter((reference) => reference.type === 'BRANCH').length,
      ),
      href: '/branches',
      icon: GitBranch,
    },
  ]
  const total = resources.every(
    (resource): resource is (typeof resources)[number] & { count: number } =>
      typeof resource.count === 'number',
  )
    ? resources.reduce((sum, resource) => sum + resource.count, 0)
    : null

  return (
    <ObservatoryPage
      kicker="Workspace"
      title="Workspace"
      description="Authored project resources, governed objects, and active change surfaces available through the current Phlo project."
      action={
        <span className="phlo-observatory-pill">
          {!environment
            ? 'Select an environment'
            : loading
              ? 'Loading'
              : total === null
                ? 'Inventory incomplete'
                : `${total} objects reported`}
        </span>
      }
    >
      <section className="phlo-observatory-command phlo-observatory-local-index-shell">
        <div className="phlo-observatory-command-primary">
          <div className="phlo-observatory-workspace-toolbar">
            <span>
              <FolderTitle />
              Project inventory
            </span>
            <Link className="phlo-observatory-map-action" to="/pipelines">
              Open pipelines
            </Link>
          </div>
          <div className="phlo-observatory-workspace-object-grid">
            {resources.map((resource) => {
              const Icon = resource.icon
              return (
                <Link
                  className="phlo-observatory-workspace-object"
                  key={resource.label}
                  to={resource.href}
                >
                  <Icon className="size-4" />
                  <span>
                    <strong>{resource.label}</strong>
                    <small>{resource.detail}</small>
                  </span>
                  <strong>
                    {resource.count === null
                      ? 'Unavailable'
                      : loading || resource.count === undefined
                        ? '—'
                        : resource.count}
                  </strong>
                </Link>
              )
            })}
          </div>
        </div>
        <aside className="phlo-observatory-inspector phlo-observatory-surface-inspector">
          <div className="phlo-observatory-inspector-label">
            Workspace scope
          </div>
          <h2>Current Phlo project</h2>
          <p>
            Counts come from the active Observatory read models. This surface
            does not claim notebook or repository objects that Phlo does not
            currently expose.
          </p>
          <div className="phlo-observatory-detail-list">
            <div className="phlo-observatory-mini-row">
              <span>Next action</span>
              <small>Inspect environment-scoped pipeline jobs</small>
            </div>
            <div className="phlo-observatory-mini-row">
              <span>Runtime evidence</span>
              <small>Open Services or Operations</small>
            </div>
          </div>
        </aside>
      </section>
    </ObservatoryPage>
  )
}

function FolderTitle() {
  return <FileCode2 className="size-4" />
}
