/** Source inventory and explicit provider-backed authoring entry points. */
import * as React from 'react'
import {
  Link,
  createFileRoute,
  redirect,
  useRouter,
} from '@tanstack/react-router'
import {
  BracesIcon,
  DatabaseIcon,
  FileIcon,
  FolderIcon,
  MailIcon,
  UploadIcon,
} from 'lucide-react'
import { z } from 'zod'
import { getSources, sourceJobIds } from '@/lib/data/api/sources'
import { PageHeader } from '@/components/phlo/page'
import { getLoads } from '@/lib/data/api/loads'
import { LoadsInventory } from '@/components/sources/loads'
import {
  getCsvUploadForNavigation,
  listCsvUploads,
} from '@/lib/data/api/uploads'
import { CsvUploadReview } from '@/components/sources/csv-upload'
import { UploadInventory } from '@/components/sources/upload-history'
import { RestApiForm } from '@/components/sources/rest-api-form'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export const Route = createFileRoute('/_app/sources')({
  validateSearch: z.object({
    tab: z
      .enum(['connected', 'add', 'upload', 'loads', 'retained'])
      .default('connected'),
    archived: z.boolean().optional(),
    upload: z
      .string()
      .regex(/^[a-f0-9]{32}$/)
      .optional(),
    cursor: z.string().optional(),
  }),
  loaderDeps: ({ search }) => ({
    env: search.env,
    cursor: search.cursor,
    upload: search.upload,
    tab: search.tab,
    archived: search.archived,
  }),
  loader: async ({ deps }) => ({
    ...(await getSources({ data: { env: deps.env, cursor: deps.cursor } })),
    loads:
      deps.tab === 'loads' ? await getLoads({ data: { env: deps.env } }) : null,
    retained:
      deps.tab === 'retained'
        ? await listCsvUploads({
            data: { env: deps.env, include_archived: deps.archived },
          })
        : null,
    upload: deps.upload
      ? await getCsvUploadForNavigation({
          data: { env: deps.env, upload_id: deps.upload },
        }).then((upload) => {
          if (upload === null)
            throw redirect({
              to: '/sources',
              search: { env: deps.env, tab: 'retained', archived: true },
            })
          return upload
        })
      : null,
  }),
  head: () => ({ meta: [{ title: 'Sources · phlo' }] }),
  component: SourcesPage,
})
const groups = [
  {
    title: 'Files',
    description: 'One-off loads or a folder Phlo watches',
    items: [
      {
        name: 'Upload a file',
        icon: FileIcon,
        description:
          'UTF-8 CSV, up to 10 MiB. Review columns and load a separate candidate.',
        reason:
          'Retain a CSV, validate every row and confirm a one-off candidate load.',
      },
      {
        name: 'Watch a folder',
        icon: FolderIcon,
        description: 'Network share or SFTP folder.',
        reason: 'Folder configuration is not available in the browser.',
      },
      {
        name: 'Object storage',
        icon: DatabaseIcon,
        description: 'S3, Azure Blob or Google Cloud Storage.',
        reason: 'Requires a configured ingestion provider.',
      },
      {
        name: 'Email inbox',
        icon: MailIcon,
        description: 'Attachments from suppliers.',
        reason: 'No email ingestion provider is exposed.',
      },
    ],
  },
  {
    title: 'Lab and manufacturing systems',
    description: 'Validated systems require an approved source contract',
    items: ['LIMS', 'Plate readers', 'Process historian', 'ELN'].map(
      (name) => ({
        name,
        icon: DatabaseIcon,
        description: 'Connect through a reviewed source-specific workflow.',
        reason: 'No configured connector authoring contract is exposed.',
      }),
    ),
  },
  {
    title: 'Databases',
    description:
      'Replication requires a configured connection and ingestion provider',
    items: [
      'PostgreSQL',
      'SQL Server',
      'Oracle',
      'Another Iceberg catalog',
    ].map((name) => ({
      name,
      icon: DatabaseIcon,
      description:
        'Inspect and configure a source through the project workflow.',
      reason: 'Connection authoring is not available in the browser.',
    })),
  },
]
function SourcesPage() {
  const data = Route.useLoaderData()
  const search = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  const [showForm, setShowForm] = React.useState(false)
  if (search.tab === 'retained' && data.retained)
    return (
      <>
        <PageHeader
          title="Retained uploads"
          crumbs={[{ label: 'Sources', to: '/sources' }]}
          meta="Private file history and archived evidence"
          actions={
            <Link to="/sources" search={{ env: data.env, tab: 'upload' }}>
              Upload a file
            </Link>
          }
        />
        <UploadInventory
          env={data.env}
          items={data.retained.items}
          total={data.retained.total}
          archived={Boolean(search.archived)}
          onReclaimed={() => void router.invalidate()}
          onArchived={(archived) =>
            void navigate({
              search: { env: data.env, tab: 'retained', archived },
            })
          }
        />
      </>
    )
  if (search.tab === 'loads' && data.loads)
    return (
      <>
        <PageHeader
          title="Loads"
          crumbs={[{ label: 'Sources', to: '/sources' }]}
          meta="Recorded files, candidate loads and ingestion runs"
          actions={
            <Button
              variant="outline"
              onClick={() =>
                void navigate({ search: { env: data.env, tab: 'connected' } })
              }
            >
              Connected sources
            </Button>
          }
        />
        <LoadsInventory key={data.env} data={data.loads} />
      </>
    )
  if (search.tab === 'upload')
    return (
      <>
        <PageHeader
          title="Upload a file"
          crumbs={[{ label: 'Sources', to: '/sources' }]}
          meta="CSV · review and load a candidate"
          actions={
            <Button
              variant="outline"
              onClick={() =>
                void navigate({ search: { env: data.env, tab: 'connected' } })
              }
            >
              Back to sources
            </Button>
          }
        />
        <CsvUploadReview
          key={search.upload ?? 'new'}
          env={data.env}
          initial={data.upload}
          onRetained={(upload) =>
            void navigate({ search: { env: data.env, tab: 'upload', upload } })
          }
          onReclaimed={() =>
            void navigate({
              search: { env: data.env, tab: 'retained', archived: true },
            })
          }
        />
      </>
    )

  return (
    <>
      <PageHeader
        title="Sources"
        meta="Where your data comes from, and how it gets into bronze"
        actions={
          <Button
            onClick={() =>
              void navigate({ search: { env: data.env, tab: 'upload' } })
            }
          >
            <UploadIcon />
            Upload a file
          </Button>
        }
      />
      <div
        className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line px-5 py-3"
        role="group"
        aria-label="Source views"
      >
        <Button
          variant={search.tab === 'connected' ? 'secondary' : 'ghost'}
          aria-pressed={search.tab === 'connected'}
          onClick={() =>
            void navigate({ search: { env: data.env, tab: 'connected' } })
          }
        >
          Connected · {data.items.length}
          {data.next_cursor ? '+' : ''}
        </Button>
        <Button
          variant={search.tab === 'add' ? 'secondary' : 'ghost'}
          aria-pressed={search.tab === 'add'}
          onClick={() =>
            void navigate({ search: { env: data.env, tab: 'add' } })
          }
        >
          Add a source
        </Button>
        <Link
          to="/sources"
          search={{ env: data.env, tab: 'loads' }}
          className="ml-2 text-[13px]"
        >
          Loads
        </Link>
        <Link
          to="/sources"
          search={{ env: data.env, tab: 'retained' }}
          className="ml-2 text-[13px]"
        >
          Retained uploads
        </Link>
      </div>
      <div className="grid min-h-0 flex-1 overflow-y-auto lg:grid-cols-[minmax(0,1fr)_300px]">
        <div className="space-y-6 p-5">
          {search.tab === 'connected' ? (
            <section aria-labelledby="connected-sources-title">
              <h2
                id="connected-sources-title"
                className="mb-3 text-[15px] font-medium"
              >
                Connected sources
              </h2>
              <p className="mb-4 text-[13px] text-muted-foreground">
                Declared source and ingestion assets in {data.env}. Registration
                does not establish connection health.
              </p>
              {data.items.length ? (
                <div className="divide-y divide-line rounded-xl border border-line">
                  {data.items.map((source) => {
                    const jobs =
                      data.jobs.state === 'available'
                        ? sourceJobIds(source, data.jobs.data.items)
                        : []
                    return (
                      <article key={source.id} className="space-y-2 p-4">
                        <div className="flex flex-wrap items-center gap-2">
                          <DatabaseIcon className="size-4 text-muted-foreground" />
                          <Link
                            to="/assets/$assetId"
                            params={{ assetId: source.id }}
                            search={{ env: data.env }}
                            className="break-all font-mono text-[13px]"
                          >
                            {source.id}
                          </Link>
                          <Badge
                            variant={
                              source.freshness_status === 'fresh'
                                ? 'ok'
                                : source.freshness_status === 'stale'
                                  ? 'bad'
                                  : 'neutral'
                            }
                          >
                            {source.freshness_status ?? 'Freshness unknown'}
                          </Badge>
                        </div>
                        <p className="text-[13px] text-muted-foreground">
                          {source.source_name ??
                            source.compute_kind ??
                            'Provider not declared'}{' '}
                          · Owner: {source.owner ?? 'Not declared'}
                        </p>
                        {source.description ? (
                          <p className="text-[13px] text-muted-foreground">
                            {source.description}
                          </p>
                        ) : null}
                        <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                          <span>
                            Last materialized:{' '}
                            {source.last_materialization_at
                              ? new Date(
                                  source.last_materialization_at,
                                ).toLocaleString('en-GB', {
                                  timeZone: 'UTC',
                                  timeZoneName: 'short',
                                })
                              : 'Not observed'}
                          </span>
                          {jobs.map((job) => (
                            <Link
                              key={job}
                              to="/pipelines/$jobName"
                              params={{ jobName: job }}
                              search={{
                                env: data.env,
                                ...(source.last_run_id
                                  ? { run: source.last_run_id }
                                  : {}),
                              }}
                            >
                              {job}
                              {source.last_run_id ? ' · inspect run' : ''}
                            </Link>
                          ))}
                        </div>
                      </article>
                    )
                  })}
                </div>
              ) : (
                <div className="rounded-xl border border-line p-6 text-sm text-muted-foreground">
                  No source or ingestion assets are registered in this
                  environment. Existing tables appear in{' '}
                  <Link to="/assets" search={{ env: data.env }}>
                    Assets
                  </Link>
                  .
                </div>
              )}
              {data.next_cursor ? (
                <Button
                  className="mt-4"
                  variant="outline"
                  onClick={() =>
                    void navigate({
                      search: {
                        env: data.env,
                        tab: 'connected',
                        cursor: data.next_cursor ?? undefined,
                      },
                    })
                  }
                >
                  Next sources
                </Button>
              ) : null}
            </section>
          ) : (
            <>
              {showForm ? (
                <RestApiForm onClose={() => setShowForm(false)} />
              ) : null}
              {groups.map((group) => (
                <section key={group.title}>
                  <div className="mb-2.5 flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <h2 className="text-[12px] tracking-wide text-muted-foreground uppercase">
                      {group.title}
                    </h2>
                    <p className="text-xs text-muted-foreground">
                      {group.description}
                    </p>
                  </div>
                  <div className="grid gap-2.5 sm:grid-cols-2 xl:grid-cols-4">
                    {group.items.map((item) => (
                      <button
                        key={item.name}
                        type="button"
                        disabled={item.name !== 'Upload a file'}
                        onClick={() =>
                          void navigate({
                            search: { env: data.env, tab: 'upload' },
                          })
                        }
                        title={item.reason}
                        className="flex min-h-32 flex-col gap-2 rounded-xl border border-line p-3.5 text-left disabled:cursor-not-allowed"
                      >
                        <span className="flex items-center gap-2 text-sm">
                          <item.icon className="size-4 text-muted-foreground" />
                          {item.name}
                        </span>
                        <span className="text-xs leading-relaxed text-muted-foreground">
                          {item.description}
                        </span>
                        <span className="mt-auto text-[11px] text-muted-foreground">
                          {item.name === 'Upload a file'
                            ? 'Retain and check columns'
                            : 'Not available here'}
                        </span>
                      </button>
                    ))}
                  </div>
                </section>
              ))}
              <section>
                <h2 className="mb-2.5 text-[12px] tracking-wide text-muted-foreground uppercase">
                  Business apps and APIs
                </h2>
                <button
                  type="button"
                  onClick={() => setShowForm(true)}
                  className="flex w-full flex-col gap-2 rounded-xl border border-line p-4 text-left hover:border-border-strong sm:max-w-sm"
                >
                  <span className="flex items-center gap-2 text-sm">
                    <BracesIcon className="size-4" />
                    REST API
                  </span>
                  <span className="text-xs text-muted-foreground">
                    Create actual DLT workflow and schema files for review.
                    Project write permission and the DLT provider are required.
                  </span>
                  <span className="text-xs text-link">Configure workflow</span>
                </button>
              </section>
            </>
          )}
        </div>
        <aside className="space-y-4 border-t border-line bg-raised p-5 text-[13px] lg:border-t-0 lg:border-l">
          <h2 className="text-[15px] font-medium">
            What adding a source sets up
          </h2>
          <ol className="list-decimal space-y-4 pl-4 text-muted-foreground">
            <li>A provider-backed ingestion workflow and a schema contract.</li>
            <li>
              Generated source files for review in this deployment's project.
            </li>
            <li>
              A workflow that can be activated and materialized after
              configuration and review.
            </li>
          </ol>
          <p className="rounded-lg bg-sunken p-3 text-xs leading-relaxed text-muted-foreground">
            Creating files does not connect a system, load data, or merge a
            branch. Credentials belong in the configured secret store.
          </p>
          <h3 className="border-t border-line pt-4 font-medium">
            Already connected?
          </h3>
          <p className="text-muted-foreground">
            Inspect a source's asset, last materialization and declared pipeline
            to trace an actual load.
          </p>
          {data.jobs.state === 'unavailable' ? (
            <p className="text-xs text-warn-ink">
              Pipeline observations are unavailable: {data.jobs.error}
            </p>
          ) : null}
        </aside>
      </div>
    </>
  )
}
