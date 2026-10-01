/** Defines the asset detail route and its tabbed operational views. */
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { z } from 'zod'
import { assetTabSchema, getAssetDetail } from '@/lib/data/api/assets'
import { KeyValues, PageHeader } from '@/components/phlo/page'
import { EmptyState } from '@/components/phlo/states'
import { Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table'
import { MaterializeDialog } from '@/components/assets/materialize-dialog'
import { BackfillDialog } from '@/components/assets/backfill-dialog'
import { AddAuditDialog } from '@/components/assets/add-audit-dialog'

export const Route = createFileRoute('/_app/assets/$assetId')({
  validateSearch: z.object({
    tab: assetTabSchema.default('overview'),
    dialog: z.enum(['materialize', 'backfill', 'audit']).optional(),
  }),
  loaderDeps: ({ search }) => ({ env: search.env, tab: search.tab }),
  loader: ({ params, deps }) =>
    getAssetDetail({ data: { id: params.assetId, ...deps } }),
  head: ({ params }) => ({ meta: [{ title: `${params.assetId} · phlo` }] }),
  component: AssetPage,
})

const tabLabels = {
  overview: 'Overview',
  data: 'Data',
  schema: 'Schema history',
  lineage: 'Lineage',
  snapshots: 'Snapshots',
  audits: 'Audits',
}

function AssetPage() {
  const result = Route.useLoaderData()
  const { asset, jobs, env } = result
  const { tab, dialog } = Route.useSearch()
  const navigate = Route.useNavigate()
  const router = useRouter()
  return (
    <>
      <PageHeader
        title={<Mono>{asset.id}</Mono>}
        actions={
          <>
            <Link to="/assets" search={{ env }}>
              All assets
            </Link>
            <Button variant="outline" onClick={() => void router.invalidate()}>
              Refresh
            </Button>
            <Button
              disabled={asset.is_source || !jobs.length}
              onClick={() =>
                void navigate({
                  search: (p) => ({ ...p, dialog: 'materialize' }),
                })
              }
            >
              Materialize
            </Button>
            <Button
              variant="outline"
              disabled={asset.is_source || !jobs.length}
              onClick={() =>
                void navigate({ search: (p) => ({ ...p, dialog: 'backfill' }) })
              }
            >
              Backfill
            </Button>
          </>
        }
      />
      <div className="flex min-h-0 flex-1 flex-col overflow-y-auto">
        <Tabs
          value={tab}
          onValueChange={(value) =>
            void navigate({
              search: (p) => ({ ...p, tab: assetTabSchema.parse(value) }),
              replace: true,
            })
          }
        >
          <div className="flex flex-col gap-2.5 border-b border-line px-4 pt-5 lg:px-7 lg:pt-[22px]">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="neutral" size="lg">
                {asset.group_name ?? 'No group'}
              </Badge>
              <Badge variant="outline" size="lg">
                {asset.compute_kind ?? 'Unknown compute kind'}
              </Badge>
              <Badge variant="outline" size="lg">
                {asset.is_source ? 'Source' : 'Computed asset'}
              </Badge>
            </div>
            <h2 className="m-0 break-all font-mono text-lg font-medium lg:text-[22px]">
              {asset.id}
            </h2>
            <p className="m-0 text-sm text-text-3">
              {asset.description ?? 'No description supplied.'}
            </p>
            <TabsList
              aria-label="Asset views"
              className="-mx-4 overflow-x-auto border-b-0 px-4 pt-2 pb-3 lg:mx-0 lg:px-0"
            >
              {assetTabSchema.options.map((value) => (
                <TabsTrigger
                  key={value}
                  value={value}
                  className="shrink-0 whitespace-nowrap"
                >
                  {tabLabels[value]}
                </TabsTrigger>
              ))}
            </TabsList>
          </div>
          <TabsContent value={tab} className="p-4 lg:p-7">
            {(function AssetOperationalViews() {
              return (
                <>
                  {result.kind === 'unavailable' ? (
                    <EmptyState
                      title="This asset view is unavailable"
                      action={
                        <Button
                          variant="outline"
                          onClick={() => void router.invalidate()}
                        >
                          Try again
                        </Button>
                      }
                    >
                      {result.message}
                    </EmptyState>
                  ) : null}
                  {result.kind === 'overview' ? (
                    <div className="flex flex-col gap-5">
                      <Card className="gap-4 p-5">
                        <KeyValues
                          keyWidth={144}
                          className="[&_dd]:block [&_dd]:break-all"
                          items={[
                            ['Environment', env],
                            ['Relation', asset.relation ?? 'Not observed'],
                            [
                              'Last materialized',
                              asset.last_materialization_at ??
                                'No materialization observed',
                            ],
                            ['Last run', asset.last_run_id ?? 'Not observed'],
                            [
                              'History scoped',
                              asset.history_scoped ? 'Yes' : 'No',
                            ],
                            [
                              'Schema observed',
                              asset.schema_observed_at ?? 'Not observed',
                            ],
                          ]}
                        />
                      </Card>
                      <Card className="gap-2 p-5">
                        <h3 className="m-0 text-sm font-medium">Jobs</h3>
                        {jobs.length ? (
                          jobs.map((job) => (
                            <Link
                              key={job.id}
                              to="/pipelines/$jobName"
                              params={{ jobName: job.id }}
                              search={{ env }}
                              className="break-all font-mono text-[13px]"
                            >
                              {job.id}
                            </Link>
                          ))
                        ) : (
                          <p className="m-0 text-sm text-muted-foreground">
                            No materialization job is available for this asset.
                          </p>
                        )}
                      </Card>
                      <Card className="overflow-auto">
                        <Table aria-label="Observed asset schema">
                          <TableHeader>
                            <TableRow>
                              <TableHead>Column</TableHead>
                              <TableHead>Type</TableHead>
                              <TableHead>Description</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {asset.columns.map((column) => (
                              <TableRow key={column.name}>
                                <TableCell>{column.name}</TableCell>
                                <TableCell>
                                  {column.type ?? 'Unknown'}
                                </TableCell>
                                <TableCell>
                                  {column.description ?? 'Not supplied'}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                        {!asset.columns.length ? (
                          <p className="px-5 text-sm text-muted-foreground">
                            No column schema has been observed.
                          </p>
                        ) : null}
                      </Card>
                    </div>
                  ) : null}
                  {result.kind === 'lineage' ? (
                    <Card className="gap-3 p-5">
                      <h3 className="m-0 text-sm font-medium">
                        Upstream dependencies
                      </h3>
                      {asset.dependencies.length ? (
                        asset.dependencies.map((key) => (
                          <Link
                            key={key.join('/')}
                            to="/assets/$assetId"
                            params={{ assetId: key.join('/') }}
                            search={{ env }}
                            className="break-all font-mono text-[13px]"
                          >
                            {key.join('/')}
                          </Link>
                        ))
                      ) : (
                        <p className="m-0 text-sm text-muted-foreground">
                          No upstream dependencies are declared.
                        </p>
                      )}
                      <p className="m-0 text-xs text-muted-foreground">
                        Downstream and column-level lineage are not shown in
                        this view.
                      </p>
                    </Card>
                  ) : null}
                  {result.kind === 'data' ? (
                    <div className="flex flex-col gap-3">
                      <p className="m-0 text-sm text-muted-foreground">
                        {result.data.rows.length} preview rows · ref{' '}
                        {result.data.nessie_ref}
                        {result.data.has_more ? ' · More rows available' : ''}
                      </p>
                      <Card className="overflow-auto">
                        <Table aria-label="Asset preview">
                          <TableHeader>
                            <TableRow>
                              {result.data.columns.map((column) => (
                                <TableHead key={column.name}>
                                  {column.name}
                                </TableHead>
                              ))}
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {result.data.rows.map((row, index) => (
                              <TableRow key={index}>
                                {result.data.columns.map((column) => (
                                  <TableCell
                                    key={column.name}
                                    className="whitespace-nowrap"
                                  >
                                    {row[column.name] === null
                                      ? 'NULL'
                                      : typeof row[column.name] === 'object'
                                        ? JSON.stringify(row[column.name])
                                        : String(row[column.name] ?? '')}
                                  </TableCell>
                                ))}
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      </Card>
                      {!result.data.rows.length ? (
                        <EmptyState title="No rows returned" />
                      ) : null}
                    </div>
                  ) : null}
                </>
              )
            })()}
            {(function AssetHistoricalViews() {
              return (
                <>
                  {result.kind === 'schema' ? (
                    <div className="flex flex-col gap-4">
                      {result.data.items.map((version) => (
                        <Card key={version.schema_id} className="gap-3 p-5">
                          <h3 className="m-0 text-sm font-medium">
                            Schema {version.schema_id}
                            {version.schema_id === result.data.current_schema_id
                              ? ' · Current'
                              : ''}
                          </h3>
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>Column</TableHead>
                                <TableHead>Type</TableHead>
                                <TableHead>Required</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {version.fields.map((field) => (
                                <TableRow key={field.name}>
                                  <TableCell>{field.name}</TableCell>
                                  <TableCell>{field.type}</TableCell>
                                  <TableCell>
                                    {field.required ? 'Yes' : 'No'}
                                  </TableCell>
                                </TableRow>
                              ))}
                            </TableBody>
                          </Table>
                        </Card>
                      ))}
                      {!result.data.items.length ? (
                        <EmptyState title="No schema history" />
                      ) : null}
                    </div>
                  ) : null}
                  {result.kind === 'snapshots' ? (
                    <Card className="overflow-auto">
                      <Table aria-label="Iceberg snapshots">
                        <TableHeader>
                          <TableRow>
                            <TableHead>Snapshot</TableHead>
                            <TableHead>Observed at</TableHead>
                            <TableHead>Operation</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {result.data.items.map((snapshot) => (
                            <TableRow key={snapshot.snapshot_id}>
                              <TableCell className="font-mono">
                                {snapshot.snapshot_id}
                              </TableCell>
                              <TableCell>
                                {new Date(snapshot.timestamp_ms).toISOString()}
                              </TableCell>
                              <TableCell>
                                {snapshot.operation ?? 'Unknown'}
                              </TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                      {!result.data.items.length ? (
                        <EmptyState title="No snapshots observed" />
                      ) : null}
                    </Card>
                  ) : null}
                  {result.kind === 'audits' ? (
                    <div className="flex flex-col gap-4">
                      <Button
                        variant="outline"
                        disabled={!asset.columns.length}
                        title={
                          !asset.columns.length
                            ? 'Observed schema evidence is required.'
                            : undefined
                        }
                        onClick={() =>
                          void navigate({
                            search: (p) => ({ ...p, dialog: 'audit' }),
                          })
                        }
                      >
                        Add audit proposal
                      </Button>
                      {!result.data.definitions.length ? (
                        <EmptyState title="No checks defined">
                          This is not a passing audit result.
                        </EmptyState>
                      ) : (
                        result.data.definitions.map((check) => (
                          <Card key={check.name} className="gap-2 p-5">
                            <h3 className="m-0 text-sm font-medium">
                              {check.name}
                            </h3>
                            <p className="m-0 text-sm text-muted-foreground">
                              {check.description ?? 'No description supplied.'}
                            </p>
                            {result.data.executions
                              .filter(
                                (execution) =>
                                  execution.check_name === check.name,
                              )
                              .map((execution) => (
                                <p
                                  key={`${execution.run_id}:${execution.timestamp}`}
                                  className="m-0 text-sm"
                                >
                                  {execution.timestamp} · {execution.status} ·{' '}
                                  {execution.passed === null
                                    ? 'Not evaluated'
                                    : execution.passed
                                      ? 'Passed'
                                      : 'Failed'}
                                </p>
                              ))}
                          </Card>
                        ))
                      )}
                    </div>
                  ) : null}
                </>
              )
            })()}
          </TabsContent>
        </Tabs>
      </div>
      <MaterializeDialog
        key={`${env}:${asset.id}`}
        open={dialog === 'materialize'}
        onClose={() =>
          void navigate({
            search: (p) => ({ ...p, dialog: undefined }),
            replace: true,
          })
        }
        assetId={asset.id}
        env={env}
        jobs={jobs.map((job) => job.id)}
      />
      <BackfillDialog
        key={`backfill:${env}:${asset.id}`}
        open={dialog === 'backfill'}
        onClose={() =>
          void navigate({
            search: (p) => ({ ...p, dialog: undefined }),
            replace: true,
          })
        }
        assetId={asset.id}
        env={env}
        jobs={jobs.map((job) => job.id)}
      />
      <AddAuditDialog
        key={`audit:${env}:${asset.id}`}
        open={dialog === 'audit'}
        onClose={() =>
          void navigate({
            search: (p) => ({ ...p, dialog: undefined }),
            replace: true,
          })
        }
        assetId={asset.id}
        env={env}
        columns={asset.columns}
      />
    </>
  )
}
