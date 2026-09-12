/**
 * /workspace route. Live resource counts across datasets, tables, pipelines,
 * branches, and saved queries as a landing index.
 */
import { Link, createFileRoute } from '@tanstack/react-router'
import {
  Boxes,
  ChevronRight,
  FileCode2,
  GitBranch,
  Table2,
  Workflow,
} from 'lucide-react'

import {
  getObservatoryBranchRecords,
  getObservatoryDatasetRecords,
  getObservatoryPipelineRecords,
  getObservatorySavedQueries,
  getObservatoryTableRecords,
} from '@/observatory/api/resources'
import { useLiveResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { buttonVariants } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { cn } from '@/lib/utils'

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
      detail: 'Governed and candidate datasets',
      count: datasets.data?.length ?? 0,
      href: '/datasets',
      icon: Boxes,
    },
    {
      label: 'Tables',
      detail: 'Queryable physical inventory',
      count: tables.data?.length ?? 0,
      href: '/tables',
      icon: Table2,
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
    <Page>
      <PageHeader
        actions={
          <>
            <Badge variant="secondary">
              {loading ? 'loading' : `${total} objects`}
            </Badge>
            <Link
              className={cn(buttonVariants({ size: 'sm' }))}
              to="/workflows/new"
            >
              <Workflow className="size-3.5" />
              New workflow
            </Link>
          </>
        }
        description="Authored project resources, governed objects, and active change surfaces in the current Phlo project."
        title="Workspace"
      />
      <SectionCard title="Project inventory">
        <div className="divide-y divide-border">
          {resources.map((resource) => {
            const Icon = resource.icon
            return (
              <Link
                className="hover:bg-accent/50 group flex items-center gap-3 px-3 py-2.5 transition-colors"
                key={resource.label}
                to={resource.href}
              >
                <span className="bg-muted text-muted-foreground flex size-7 flex-none items-center justify-center rounded-none">
                  <Icon className="size-3.5" />
                </span>
                <span className="min-w-0 flex-1">
                  <span className="text-foreground block text-xs font-medium">
                    {resource.label}
                  </span>
                  <span className="text-muted-foreground block truncate text-[11px]">
                    {resource.detail}
                  </span>
                </span>
                {loading ? (
                  <Skeleton className="h-4 w-8" />
                ) : (
                  <span className="text-foreground tabular font-mono text-sm font-semibold">
                    {resource.count}
                  </span>
                )}
                <ChevronRight className="text-muted-foreground size-3.5" />
              </Link>
            )
          })}
        </div>
      </SectionCard>
    </Page>
  )
}
