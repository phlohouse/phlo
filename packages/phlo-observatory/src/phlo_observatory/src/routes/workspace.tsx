/**
 * /workspace route. Live resource counts across datasets, tables, pipelines,
 * branches, and saved queries as a landing index.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import { Boxes, Database, FileCode2, GitBranch, Workflow } from 'lucide-react'

import {
  getObservatoryBranchRecords,
  getObservatoryDatasetRecords,
  getObservatoryPipelineRecords,
  getObservatorySavedQueries,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { ObservatoryPage } from '@/observatory/components/ObservatoryPage'
import { useLiveResource } from '@/observatory/routes/liveResource'

export const Route = createFileRoute('/workspace')({ component: Workspace })

export function Workspace() {
  const datasets = useLiveResource(
    getObservatoryDatasetRecords,
    60_000,
    'observatory:datasets',
  )
  const tables = useLiveResource(
    getObservatoryTableRecords,
    60_000,
    'observatory:tables',
  )
  const pipelines = useLiveResource(
    getObservatoryPipelineRecords,
    60_000,
    'observatory:pipelines',
  )
  const branches = useLiveResource(
    getObservatoryBranchRecords,
    60_000,
    'observatory:branches',
  )
  const queries = useLiveResource(
    getObservatorySavedQueries,
    30_000,
    'observatory:saved-queries',
  )
  const loading = [datasets, tables, pipelines, branches, queries].some(
    (item) => item.isLoading,
  )

  const resources = [
    {
      label: 'Datasets',
      detail: 'Governed and candidate data products',
      count: datasets.data?.length ?? 0,
      href: '/datasets',
      icon: Boxes,
    },
    {
      label: 'Tables',
      detail: 'Queryable physical inventory',
      count: tables.data?.length ?? 0,
      href: '/tables',
      icon: Database,
    },
    {
      label: 'Pipelines',
      detail: 'Dataset refresh and stage definitions',
      count: pipelines.data?.length ?? 0,
      href: '/pipelines',
      icon: Workflow,
    },
    {
      label: 'Saved queries',
      detail: 'Read-only SQL workspace objects',
      count: queries.data?.length ?? 0,
      href: '/queries',
      icon: FileCode2,
    },
    {
      label: 'Change reviews',
      detail: 'Branches and proposed lakehouse changes',
      count: branches.data?.length ?? 0,
      href: '/branches',
      icon: GitBranch,
    },
  ]
  const total = resources.reduce((sum, item) => sum + item.count, 0)

  return (
    <ObservatoryPage
      kicker="Workspace"
      title="Workspace"
      description="Authored project resources, governed objects, and active change surfaces available through the current Phlo project."
      action={
        <span className="phlo-observatory-pill">
          {loading ? 'Loading' : `${total} objects`}
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
            <Link className="phlo-observatory-map-action" to="/workflows/new">
              Create workflow
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
                  <strong>{loading ? '—' : resource.count}</strong>
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
              <small>Create or inspect an authored workflow</small>
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
