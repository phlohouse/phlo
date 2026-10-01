/** Renders the overview, data, schema, lineage, and snapshot tabs for an asset. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import {
  CircleCheckIcon,
  CircleXIcon,
  CodeIcon,
  PlusIcon,
  XIcon,
} from 'lucide-react'
import { Bar, BarChart, CartesianGrid, XAxis, YAxis } from 'recharts'
import type { Asset, AssetDetail } from '@/lib/data/types'
import type { ChartConfig } from '@/components/ui/chart'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import { Stat } from '@/components/phlo/kpi'
import { Dot, LayerSwatch, Mono, RichText } from '@/components/phlo/status'
import { EmptyState } from '@/components/phlo/states'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Segmented } from '@/components/ui/toggle-group'
import {
  ChartContainer,
  ChartTooltip,
  ChartTooltipContent,
} from '@/components/ui/chart'
import { cn } from '@/lib/utils'
import { LineageGraph } from '@/components/assets/lineage-graph'

type P = { asset: Asset; detail: AssetDetail }

const pad = 'px-4 py-5 lg:px-7'

/* ============================== Overview ============================== */
export function OverviewTab({ asset, detail }: P) {
  const d = detail
  return (
    <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[minmax(0,1fr)_360px]">
      <section className={cn(pad, 'flex min-w-0 flex-col gap-[22px]')}>
        <RowsPerRun detail={d} />
        <div className="flex flex-col">
          <div className="flex items-baseline pb-1.5">
            <Eyebrow>Schema · {d.columns.length} columns</Eyebrow>
            <span className="ml-auto text-[13px] text-muted-foreground">
              Contract: <Mono className="text-xs">{d.contract}</Mono>
            </span>
          </div>
          <div className="overflow-x-auto">
            <div className="min-w-[520px]" role="table" aria-label="Schema">
              <div
                role="row"
                className="grid h-8 grid-cols-[150px_100px_72px_minmax(0,1fr)] items-center gap-x-3.5 border-b border-line-soft text-xs text-muted-foreground"
              >
                <span role="columnheader">Column</span>
                <span role="columnheader">Type</span>
                <span role="columnheader">Nullable</span>
                <span role="columnheader">Notes</span>
              </div>
              {d.columns.map((c, i) => (
                <div
                  key={c.name}
                  role="row"
                  className={cn(
                    'grid h-10 grid-cols-[150px_100px_72px_minmax(0,1fr)] items-center gap-x-3.5 text-[13.5px]',
                    i < d.columns.length - 1 && 'border-b border-line-soft',
                    c.renamedTo && 'bg-warn-wash',
                  )}
                >
                  <span
                    role="cell"
                    className="truncate font-mono text-[12.5px]"
                  >
                    {c.name}
                  </span>
                  <span
                    role="cell"
                    className="font-mono text-[12.5px] text-muted-foreground"
                  >
                    {c.type}
                  </span>
                  <span role="cell" className="text-muted-foreground">
                    {c.nullable ? 'yes' : 'no'}
                  </span>
                  <span
                    role="cell"
                    className="flex min-w-0 items-center gap-2 text-muted-foreground"
                  >
                    {c.renamedTo ? (
                      <>
                        <Badge variant="branch">renamed on fix branch</Badge>
                        <Mono className="text-xs">→ {c.renamedTo}</Mono>
                      </>
                    ) : (
                      <span className="truncate">{c.note}</span>
                    )}
                  </span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      <aside className="flex flex-col gap-[22px] border-t border-line px-4 py-5 lg:border-t-0 lg:border-l lg:px-6">
        <KeyValues
          keyWidth={112}
          className="text-[13.5px]"
          items={[
            ['Owner', asset.owner],
            ['Source', d.source],
            [
              'Job',
              <Link
                key="j"
                to="/pipelines/$jobName"
                params={{ jobName: d.job }}
                className="font-mono text-[12.5px]"
              >
                {d.job}
              </Link>,
            ],
            ['Freshness SLA', d.sla],
            ['Rows', <Mono key="r">{asset.rows}</Mono>],
            ['Size', <Mono key="s">{d.files}</Mono>],
            ['Sort order', <Mono key="o">{d.sortOrder}</Mono>],
          ]}
        />
        <div className="flex flex-col">
          <Eyebrow className="pb-1">Audits</Eyebrow>
          {d.overviewAudits.map((a, i) => (
            <div
              key={a.name}
              className={cn(
                'flex h-9 items-center gap-2.5 text-[13.5px]',
                i < d.overviewAudits.length - 1 && 'border-b border-line-soft',
              )}
            >
              {a.failing ? (
                <CircleXIcon
                  className="size-3.5 shrink-0 text-bad"
                  aria-label="Failing"
                />
              ) : (
                <CircleCheckIcon
                  className="size-3.5 shrink-0 text-ok"
                  aria-label="Passing"
                />
              )}
              <Mono className="truncate text-[12.5px]">{a.name}</Mono>
              {a.failing ? (
                <span className="ml-auto text-xs text-bad-text">failing</span>
              ) : null}
            </div>
          ))}
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex items-baseline">
            <Eyebrow>Downstream</Eyebrow>
            <Link
              from="/assets/$assetId"
              to="."
              search={(p) => ({ ...p, tab: 'lineage' as const })}
              className="ml-auto text-[13px]"
            >
              Blast radius
            </Link>
          </div>
          {d.downstream.length ? (
            d.downstream.map((x) => (
              <Link
                key={x.id}
                to="/assets/$assetId"
                params={{ assetId: x.id }}
                className="flex min-w-0 items-center gap-2 text-foreground hover:text-link"
              >
                <LayerSwatch layer={x.layer} />
                <Mono className="truncate text-[12.5px]">{x.id}</Mono>
              </Link>
            ))
          ) : (
            <span className="text-[13px] text-muted-foreground">
              Nothing reads from this table yet.
            </span>
          )}
          {d.downstreamMore ? (
            <span className="text-[13px] text-muted-foreground">
              {d.downstreamMore}
            </span>
          ) : null}
        </div>
      </aside>
    </div>
  )
}

const rowsChartConfig = {
  loaded: { label: 'Loaded', color: 'var(--bar)' },
  skipped: { label: 'Skipped', color: 'var(--skip-line)' },
  failed: { label: 'Failed', color: 'var(--bad)' },
} satisfies ChartConfig

/** "2 h 15 m ago" for run i of n, one run every 15 minutes, newest last. */
function runAgo(i: number, n: number) {
  const m = (n - 1 - i) * 15
  if (m === 0) return 'Latest run'
  const h = Math.floor(m / 60)
  return `${h ? `${h} h ` : ''}${m % 60 ? `${m % 60} m ` : ''}ago`
}

function RowsPerRun({ detail }: { detail: AssetDetail }) {
  const ok = detail.runs.filter((r) => r.state === 'ok').map((r) => r.value)
  const max = Math.max(...ok) / 0.9
  const usual = ok.length ? ok.reduce((a, v) => a + v, 0) / ok.length : 1
  const failed = detail.runs.filter((r) => r.state === 'fail').length
  const skipped = detail.runs.filter((r) => r.state === 'skip').length
  const rows = React.useMemo(
    () =>
      detail.runs.map((r, i) => {
        const v = Math.max(max * 0.08, Math.min(max, r.value))
        return {
          run: runAgo(i, detail.runs.length),
          loaded: r.state === 'ok' ? v : undefined,
          skipped: r.state === 'skip' ? v : undefined,
          failed: r.state === 'fail' ? v : undefined,
        }
      }),
    [detail.runs, max],
  )
  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1.5">
        <Eyebrow>Rows per run · last {detail.runs.length} runs</Eyebrow>
        <div className="ml-auto flex items-center gap-3.5 text-[13px] text-muted-foreground">
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px] bg-bar" /> Loaded
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px] border border-dashed border-skip-line" />{' '}
            Skipped
          </span>
          <span className="flex items-center gap-1.5">
            <span className="size-2 rounded-[2px] bg-bad" /> Failed
          </span>
        </div>
      </div>
      <ChartContainer
        config={rowsChartConfig}
        label={`Rows per run, last ${detail.runs.length} runs: ${skipped} skipped, ${failed} failed`}
        className="aspect-auto h-24 w-full"
      >
        <BarChart
          data={rows}
          margin={{ top: 0, right: 0, bottom: 0, left: 0 }}
          barCategoryGap="12%"
        >
          <CartesianGrid vertical={false} horizontalValues={[0]} />
          <XAxis dataKey="run" hide />
          <YAxis hide domain={[0, max]} />
          <ChartTooltip
            cursor={false}
            content={
              <ChartTooltipContent
                valueFormatter={(v, key) =>
                  key === 'loaded'
                    ? `${Math.round((Number(v) / usual) * 100)}% of usual`
                    : key === 'skipped'
                      ? 'no new rows'
                      : 'nothing written'
                }
              />
            }
          />
          <Bar
            dataKey="loaded"
            stackId="run"
            fill="var(--color-loaded)"
            radius={[2, 2, 0, 0]}
            isAnimationActive={false}
          />
          <Bar
            dataKey="skipped"
            stackId="run"
            fill="transparent"
            stroke="var(--color-skipped)"
            strokeDasharray="3 2"
            radius={[2, 2, 0, 0]}
            isAnimationActive={false}
          />
          <Bar
            dataKey="failed"
            stackId="run"
            fill="var(--color-failed)"
            radius={[2, 2, 0, 0]}
            isAnimationActive={false}
          />
        </BarChart>
      </ChartContainer>
      <div className="flex justify-between font-mono text-[11px] text-muted-foreground">
        <span>12 h ago</span>
        <span>6 h ago</span>
        <span>now</span>
      </div>
    </div>
  )
}

/* ============================== Data ============================== */
export function DataTab({ asset, detail }: P) {
  const d = detail
  const [filters, setFilters] = React.useState(d.data.filters)
  const [branch, setBranch] = React.useState(d.data.branches[0] ?? 'main')
  const onFix = branch !== 'main'
  const numeric = (t: string) => t === 'double' || t === 'int'
  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-3 lg:px-7">
        {filters.map((f) => (
          <span
            key={f.column}
            className="flex h-7 items-center gap-1.5 rounded-full border border-border bg-card pr-1 pl-3 text-[13px] text-text-2"
          >
            <span className="text-muted-foreground">{f.column}</span>
            {f.label}
            {f.value ? <Mono className="text-[12.5px]">{f.value}</Mono> : null}
            <button
              type="button"
              aria-label={`Remove filter on ${f.column}`}
              onClick={() =>
                setFilters((xs) => xs.filter((x) => x.column !== f.column))
              }
              className="inline-flex size-5 cursor-pointer items-center justify-center rounded-full text-muted-foreground hover:bg-soft"
            >
              <XIcon className="size-3" />
            </button>
          </span>
        ))}
        <button
          type="button"
          className="flex h-7 cursor-pointer items-center gap-1 rounded-full border border-dashed border-skip-line bg-card px-3 text-[13px] text-text-2 hover:bg-soft"
        >
          <PlusIcon className="size-3" /> Filter
        </button>
        <span className="ml-2 text-[13px] text-muted-foreground">
          Columns: {d.columns.length} of {d.columns.length}
        </span>
        <div className="flex w-full flex-wrap items-center gap-2 sm:ml-auto sm:w-auto">
          {d.data.branches.length > 1 ? (
            <Segmented
              aria-label="Branch"
              value={branch}
              onValueChange={setBranch}
              options={d.data.branches.map((b) => ({
                value: b,
                label: <Mono className="text-xs">{b}</Mono>,
              }))}
            />
          ) : null}
          <Link
            to="/query"
            className={cn(buttonVariants({ variant: 'outline' }), 'h-[30px]')}
          >
            <CodeIcon /> Open in Query
          </Link>
        </div>
      </div>
      <div className="overflow-x-auto">
        <table
          className="w-full min-w-max border-collapse font-mono text-[12.5px]"
          aria-label={`Sample rows from ${asset.id}`}
        >
          <thead>
            <tr className="bg-raised">
              <th
                scope="col"
                className="h-[34px] w-12 border-r border-b border-r-line-soft border-b-line px-3 text-right font-sans text-xs font-normal text-muted-foreground"
              >
                #
              </th>
              {d.columns.map((c, i) => (
                <th
                  key={c.name}
                  scope="col"
                  className={cn(
                    'h-[34px] border-b border-b-line px-3 font-sans text-xs font-normal whitespace-nowrap text-muted-foreground',
                    i < d.columns.length - 1 && 'border-r border-r-line-soft',
                    numeric(c.type) ? 'text-right' : 'text-left',
                  )}
                >
                  {onFix && c.renamedTo ? c.renamedTo : c.name}{' '}
                  <span className="ml-1 font-mono text-[10.5px] text-faint">
                    {c.type}
                  </span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {d.data.rows.map((r) => (
              <tr key={r[0]}>
                {r.map((v, i) => {
                  const col = d.columns[i - 1]
                  return (
                    <td
                      key={i}
                      className={cn(
                        'h-8 border-b border-line-soft px-3 whitespace-nowrap',
                        i < r.length - 1 && 'border-r',
                        i === 0
                          ? 'text-right text-faint'
                          : col && numeric(col.type)
                            ? 'text-right text-foreground'
                            : 'text-text-2',
                        col?.name === '_dlt_load_id' && 'text-muted-foreground',
                      )}
                    >
                      {v}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 border-t border-line px-4 py-3 text-[13px] text-muted-foreground lg:px-7">
        <span>{d.data.shown}</span>
        <span>
          As of snapshot <Mono className="text-xs">{d.data.asOf}</Mono> on{' '}
          <Mono className="text-xs">{branch}</Mono>
        </span>
        {d.data.newest ? (
          <span
            className={cn(
              'sm:ml-auto',
              d.data.newest.tone === 'warn' && 'text-warn-ink',
            )}
          >
            {d.data.newest.text}
          </span>
        ) : null}
      </div>
    </div>
  )
}

/* ============================== Schema history ============================== */
export function SchemaTab({ detail }: P) {
  const s = detail.schema
  return (
    <div className="grid min-h-0 grid-cols-1 lg:grid-cols-[380px_minmax(0,1fr)]">
      <section
        aria-label="Schema versions"
        className="flex flex-col gap-1 border-b border-line px-3 py-4 lg:border-r lg:border-b-0 lg:px-4 lg:py-[18px]"
      >
        <Eyebrow className="px-2.5 pb-2">{s.count} versions</Eyebrow>
        {s.versions.map((v) => (
          <div
            key={v.version}
            aria-current={v.current ? 'true' : undefined}
            className={cn(
              'flex flex-col gap-1 rounded-[10px] border border-transparent p-3',
              v.current && 'border-branch-line bg-branch-soft',
            )}
          >
            <div className="flex items-center gap-2">
              <Mono className="text-[12.5px] font-medium">{v.version}</Mono>
              {v.ref ? (
                v.ref.tone === 'branch' ? (
                  <span className="rounded-[5px] border border-branch-line bg-card px-1.5 text-xs text-branch">
                    {v.ref.label}
                  </span>
                ) : (
                  <Badge variant={v.ref.tone} className="px-1.5 py-0">
                    {v.ref.label}
                  </Badge>
                )
              ) : null}
              <span className="ml-auto text-xs text-muted-foreground">
                {v.when}
              </span>
            </div>
            <RichText text={v.change} className="text-sm" />
            <div className="text-xs text-muted-foreground">{v.note}</div>
          </div>
        ))}
      </section>
      <section className={cn(pad, 'flex min-w-0 flex-col gap-5')}>
        <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-2">
          <div className="text-[15px] font-medium">{s.diffTitle}</div>
          <div className="text-[13px] text-muted-foreground">
            Compared with <Mono className="text-xs">main</Mono>
          </div>
          {s.diffBadge ? (
            <Badge variant={s.diffBadge.tone} className="sm:ml-auto">
              {s.diffBadge.label}
            </Badge>
          ) : null}
        </div>
        <div className="overflow-x-auto rounded-[10px] border border-border-card">
          <div
            className="min-w-[520px] py-1.5 font-mono text-[12.5px] leading-[30px]"
            role="table"
            aria-label="Schema diff"
          >
            {s.diff.map((l) => (
              <div
                key={l.kind + l.name}
                role="row"
                className={cn(
                  'grid grid-cols-[24px_180px_120px_1fr] px-3.5 text-text-2',
                  l.kind === '-' &&
                    'border-l-2 border-bad bg-bad-wash pl-3 text-bad-ink',
                  l.kind === '+' &&
                    'border-l-2 border-ok bg-ok-soft pl-3 text-ok-ink',
                )}
              >
                <span
                  role="cell"
                  aria-label={
                    l.kind === '+'
                      ? 'added'
                      : l.kind === '-'
                        ? 'removed'
                        : undefined
                  }
                >
                  {l.kind === '-' ? '−' : l.kind}
                </span>
                <span role="cell">{l.name}</span>
                <span
                  role="cell"
                  className={
                    l.kind === ' ' ? 'text-muted-foreground' : undefined
                  }
                >
                  {l.type}
                </span>
                <span
                  role="cell"
                  className={
                    l.kind === ' ' ? 'text-muted-foreground' : undefined
                  }
                >
                  {l.extra}
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="flex flex-col gap-2">
          <Eyebrow>{s.usagesTitle}</Eyebrow>
          {s.usages.length ? (
            s.usages.map((u, i) => (
              <div
                key={u.file}
                className={cn(
                  'flex flex-wrap items-center gap-x-2.5 gap-y-1 py-2.5 text-[13.5px]',
                  i < s.usages.length - 1 && 'border-b border-line-soft',
                )}
              >
                <Mono className="min-w-0 text-[12.5px] break-all">
                  {u.file}
                </Mono>
                <span className="text-[13px] text-muted-foreground">
                  {u.where}
                </span>
                <Badge variant={u.status.tone} className="ml-auto">
                  {u.status.label}
                </Badge>
              </div>
            ))
          ) : (
            <EmptyState title="Nothing downstream reads it">
              This change doesn't touch any downstream model.
            </EmptyState>
          )}
        </div>
      </section>
    </div>
  )
}

/* ============================== Lineage ============================== */
export function LineageTab({ detail }: P) {
  const d = detail
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-[18px]')}>
      <div className="flex flex-wrap items-baseline gap-x-2.5">
        <div className="text-[15px] font-medium">Upstream and downstream</div>
        <div className="text-[13px] text-muted-foreground">
          Red outline means stale right now
        </div>
      </div>
      <LineageGraph
        columns={d.lineage.columns}
        edges={d.lineage.edges}
        label={d.lineage.label}
      />
      <div className="grid grid-cols-1 gap-6 border-t border-line pt-4 md:grid-cols-2">
        <div className="flex flex-col">
          <Eyebrow className="pb-1.5">Column-level use</Eyebrow>
          {d.columnUse.map(([cols, use], i) => (
            <div
              key={cols}
              className={cn(
                'flex min-h-[38px] items-center gap-2.5 py-1.5 text-[13.5px]',
                i < d.columnUse.length - 1 && 'border-b border-line-soft',
              )}
            >
              <Mono className="w-[130px] shrink-0 text-[12.5px]">{cols}</Mono>
              <span className="text-[13px] text-muted-foreground">{use}</span>
            </div>
          ))}
        </div>
        <div className="flex flex-col">
          <Eyebrow className="pb-1.5">
            Reports that depend on this table
          </Eyebrow>
          {d.reports.length ? (
            d.reports.map((r, i) => (
              <div
                key={r.name}
                className={cn(
                  'flex h-[38px] items-center gap-2.5 text-[13.5px]',
                  i < d.reports.length - 1 && 'border-b border-line-soft',
                )}
              >
                <LayerSwatch layer="gold" />
                {r.name}
                {r.status.tone ? (
                  <Badge variant={r.status.tone} className="ml-auto">
                    {r.status.label}
                  </Badge>
                ) : (
                  <span className="ml-auto text-[13px] text-muted-foreground">
                    {r.status.label}
                  </span>
                )}
              </div>
            ))
          ) : (
            <span className="py-2 text-[13px] text-muted-foreground">
              No reports read this table directly or through gold.
            </span>
          )}
        </div>
      </div>
    </div>
  )
}

/* ============================== Snapshots ============================== */
const refVariant = { main: 'neutral', fix: 'branch', tag: 'warn' } as const

export function SnapshotsTab({ asset, detail }: P) {
  const d = detail
  const cols =
    'grid grid-cols-[140px_110px_160px_100px_70px_190px_minmax(0,1fr)] items-center gap-x-3.5'
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-[18px]')}>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {d.snapshotStats.map((s) => (
          <Stat key={s.label} label={s.label} value={s.value} tone={s.tone} />
        ))}
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[1000px]" role="table" aria-label="Snapshots">
          <div
            role="row"
            className={cn(
              cols,
              'h-[34px] border-b border-line text-xs text-muted-foreground',
            )}
          >
            <span role="columnheader">Snapshot</span>
            <span role="columnheader">Committed</span>
            <span role="columnheader">Operation</span>
            <span role="columnheader" className="text-right">
              Rows added
            </span>
            <span role="columnheader" className="text-right">
              Files
            </span>
            <span role="columnheader">Ref</span>
            <span role="columnheader">By</span>
          </div>
          {d.snapshots.map((s) => (
            <div
              key={s.id}
              role="row"
              className={cn(
                cols,
                'h-11 border-b border-line-soft text-[13.5px]',
              )}
            >
              <Mono className="text-[12.5px]">{s.id}</Mono>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {s.when}
              </span>
              <span role="cell">{s.op}</span>
              <span
                role="cell"
                className={cn(
                  'text-right font-mono text-[12.5px]',
                  s.rows.startsWith('+')
                    ? 'text-ok-text'
                    : 'text-muted-foreground',
                )}
              >
                {s.rows}
              </span>
              <span
                role="cell"
                className="text-right font-mono text-[12.5px] text-muted-foreground"
              >
                {s.files}
              </span>
              <span role="cell">
                <Badge variant={refVariant[s.refKind]} className="font-mono">
                  {s.ref}
                </Badge>
              </span>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {s.by}
              </span>
            </div>
          ))}
        </div>
      </div>
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
        <code className="block min-w-0 flex-1 rounded-[10px] border border-border-card bg-sunken px-3.5 py-3 font-mono text-[12.5px] leading-[1.7] break-all whitespace-pre-wrap text-text-2">
          <span className="text-code-kw">SELECT</span> *{' '}
          <span className="text-code-kw">FROM</span> {asset.id}{' '}
          <span className="text-code-kw">FOR VERSION AS OF</span>{' '}
          {d.timeTravelId}
        </code>
        <Button variant="outline" className="h-10 shrink-0">
          Roll back to this snapshot
        </Button>
      </div>
    </div>
  )
}

/* ============================== Audits ============================== */
const histCell: Record<string, string> = {
  p: 'bg-ok-bar',
  f: 'bg-bad',
  w: 'bg-warn-bar',
  k: 'bg-border',
}
const histWord: Record<string, string> = {
  p: 'passed',
  f: 'failed',
  w: 'warned',
  k: 'skipped',
}

export function AuditsTab({
  detail,
  addAudit,
}: P & { addAudit: React.ReactNode }) {
  const a = detail.audits
  const failing = a.filter((x) => x.failing).length
  const cols =
    'grid grid-cols-[28px_minmax(180px,1fr)_110px_150px_260px_120px] items-center gap-x-3.5'
  return (
    <div className={cn(pad, 'flex min-h-0 flex-col gap-4')}>
      <div className="flex flex-wrap items-baseline gap-x-2.5 gap-y-2">
        <div className="text-[15px] font-medium">{a.length} audits</div>
        <div className="text-[13px] text-muted-foreground">
          Run after every load · {a.length - failing} passing, {failing} failing
        </div>
        <div className="ml-auto">{addAudit}</div>
      </div>
      <div className="overflow-x-auto">
        <div className="min-w-[900px]" role="table" aria-label="Audits">
          <div
            role="row"
            className={cn(
              cols,
              'h-[34px] border-b border-line text-xs text-muted-foreground',
            )}
          >
            <span role="columnheader">
              <span className="sr-only">Status</span>
            </span>
            <span role="columnheader">Audit</span>
            <span role="columnheader">Kind</span>
            <span role="columnheader">Last result</span>
            <span role="columnheader">Last 20 loads</span>
            <span role="columnheader">Blocks</span>
          </div>
          {a.map((x) => (
            <div
              key={x.name}
              role="row"
              className={cn(cols, 'h-[50px] border-b border-line-soft')}
            >
              <span role="cell">
                <Dot tone={x.failing ? 'bad' : 'ok'} className="size-2.5" />
              </span>
              <Mono className="truncate text-[12.5px]">{x.name}</Mono>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {x.kind}
              </span>
              <span
                role="cell"
                className={cn(
                  'text-[13.5px]',
                  x.failing
                    ? 'text-bad-text'
                    : x.result.startsWith('Warning')
                      ? 'text-warn-ink'
                      : 'text-ok-text',
                )}
              >
                {x.result}
              </span>
              <span
                role="img"
                aria-label={`Last 20 loads: ${['p', 'w', 'f', 'k']
                  .map(
                    (k) =>
                      [
                        k,
                        x.history.split('').filter((h) => h === k).length,
                      ] as const,
                  )
                  .filter(([, n]) => n)
                  .map(([k, n]) => `${n} ${histWord[k]}`)
                  .join(', ')}`}
                className="flex gap-[3px]"
              >
                {x.history.split('').map((h, i) => (
                  <span
                    key={i}
                    className={cn(
                      'h-[18px] w-[9px] rounded-[2px]',
                      histCell[h],
                    )}
                  />
                ))}
              </span>
              <span role="cell" className="text-[13px] text-muted-foreground">
                {x.blocks}
              </span>
            </div>
          ))}
        </div>
      </div>
      <p className="m-0 text-[13px] leading-normal text-muted-foreground">
        Blocking audits stop downstream models from reading a load that failed.
        Warnings open an incident but let data through.
      </p>
    </div>
  )
}
