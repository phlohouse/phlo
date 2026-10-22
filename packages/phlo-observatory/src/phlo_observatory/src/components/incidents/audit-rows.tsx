/** Investigates current failing rows using the existing scoped query service. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { ExternalLinkIcon, PlayIcon } from 'lucide-react'
import type { QueryResult, QuerySession } from '@/lib/data/api/query'
import { getQuerySession, submitQuery } from '@/lib/data/api/query'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

export function incidentFailureSql(
  relation: string,
  columns: Array<{ name: string; type: string | null }>,
  check: unknown,
) {
  const result = columns.find((column) => column.name === 'in_spec')
  if (check !== 'within_spec' || !result || !/bool/i.test(result.type ?? ''))
    return undefined
  const quoted = relation
    .split('.')
    .map((part) => `"${part.replaceAll('"', '""')}"`)
    .join('.')
  const order = columns.some((column) => column.name === 'sample_id')
    ? '\nORDER BY "sample_id"'
    : ''
  return `SELECT *\nFROM ${quoted}\nWHERE "in_spec" = FALSE${order}\nLIMIT 100`
}

function useIncidentQuery(env: string) {
  const [session, setSession] = React.useState<QuerySession>()
  const [error, setError] = React.useState<string>()
  const [submitting, setSubmitting] = React.useState(false)
  const mounted = React.useRef(true)
  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const running =
    session && ['queued', 'running', 'cancelling'].includes(session.status)
  React.useEffect(() => {
    if (!running || !session) return
    let active = true
    const timer = setTimeout(() => {
      void getQuerySession({ data: { env, id: session.id } })
        .then((next) => {
          if (active) {
            setSession(next)
            setError(undefined)
          }
        })
        .catch((caught: unknown) => {
          if (active)
            setError(
              caught instanceof Error
                ? caught.message
                : 'Could not refresh query status.',
            )
        })
    }, 600)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [env, session, running])
  async function start(sql: string) {
    setSubmitting(true)
    setError(undefined)
    try {
      const next =
        running && session
          ? await getQuerySession({ data: { env, id: session.id } })
          : await submitQuery({ data: { env, sql } })
      if (mounted.current) setSession(next)
    } catch (caught) {
      if (mounted.current)
        setError(
          caught instanceof Error
            ? caught.message
            : 'Could not load failing rows.',
        )
    } finally {
      if (mounted.current) setSubmitting(false)
    }
  }
  return { session, error, pending: submitting || (!!running && !error), start }
}

function AuditFailureGroups({ result }: { result: QueryResult }) {
  const column = ['batch_id', 'assay', 'analyst'].find((name) =>
    result.columns.some((item) => item.name === name),
  )
  if (!column || !result.rows.length) return null
  const counts = new Map<string, number>()
  for (const row of result.rows) {
    const value = String(row[column] ?? 'Unknown')
    counts.set(value, (counts.get(value) ?? 0) + 1)
  }
  const groups = [...counts].sort((a, b) => b[1] - a[1]).slice(0, 8)
  const max = Math.max(...groups.map(([, count]) => count))
  return (
    <section aria-label="Failure distribution" className="space-y-3">
      <h3 className="m-0 text-sm font-medium">
        Where the failures come from{' '}
        <span className="font-normal text-muted-foreground">
          · by {column.replaceAll('_', ' ')}
        </span>
      </h3>
      <ul className="m-0 list-none space-y-2 p-0">
        {groups.map(([label, count]) => (
          <li
            key={label}
            className="grid grid-cols-[minmax(80px,130px)_1fr_36px] items-center gap-3 text-xs"
          >
            <span className="truncate font-mono" title={label}>
              {label}
            </span>
            <span className="h-2 overflow-hidden rounded-sm bg-raised">
              <span
                className="block h-full rounded-sm bg-bad"
                style={{ width: `${(count / max) * 100}%` }}
              />
            </span>
            <span className="text-right font-mono tabular-nums">{count}</span>
          </li>
        ))}
      </ul>
      <p className="m-0 text-xs text-muted-foreground">
        Counts describe{' '}
        {result.has_more ? 'the first 100 returned' : 'the returned'} current
        failing rows
        {groups.length < counts.size
          ? `. Showing the 8 largest of ${counts.size} groups`
          : ''}
        .
      </p>
    </section>
  )
}

function FailingRowsTable({ result }: { result: QueryResult }) {
  const preferred = [
    'sample_id',
    'batch_id',
    'plate_id',
    'assay',
    'result_value',
    'spec_lower',
    'spec_upper',
  ]
  const columns = preferred.filter((name) =>
    result.columns.some((column) => column.name === name),
  )
  const visible = columns.length
    ? columns
    : result.columns.slice(0, 7).map((column) => column.name)
  return (
    <div
      role="region"
      aria-label="Current failing rows"
      tabIndex={0}
      className="overflow-x-auto rounded-lg border border-line focus-visible:outline-2 focus-visible:outline-ring"
    >
      <table className="w-full whitespace-nowrap text-left text-xs">
        <caption className="sr-only">
          First eight current rows failing the within_spec check
        </caption>
        <thead className="bg-raised text-muted-foreground">
          <tr>
            {visible.map((name) => (
              <th
                key={name}
                scope="col"
                className="border-r border-b border-line px-3 py-2 font-normal"
              >
                {name}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {result.rows.slice(0, 8).map((row, index) => (
            <tr key={index} className="font-mono hover:bg-raised">
              {visible.map((name) => (
                <td
                  key={name}
                  className={`border-r border-b border-line-soft px-3 py-2 ${name === 'result_value' ? 'text-bad-text' : ''}`}
                >
                  {String(row[name] ?? 'NULL')}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function AuditRowsResult({
  result,
}: {
  result: QueryResult | null | undefined
}) {
  if (!result) return null
  if (!result.rows.length)
    return (
      <p className="rounded-lg border border-line p-4 text-sm">
        No rows currently fail this predicate. The original incident evidence
        remains unchanged.
      </p>
    )
  return (
    <>
      <FailingRowsTable result={result} />
      <AuditFailureGroups result={result} />
    </>
  )
}

export function AuditRows({ env, sql }: { env: string; sql: string }) {
  const { session, error, pending, start } = useIncidentQuery(env)
  const result = session?.status === 'completed' ? session.result : undefined
  return (
    <section aria-label="Failing row investigation" className="space-y-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="m-0 text-sm font-medium">Failing rows</h3>
          {result ? (
            <span className="text-xs text-muted-foreground">
              {result.rows.length}
              {result.has_more ? '+' : ''} rows · showing{' '}
              {Math.min(result.rows.length, 8)}
            </span>
          ) : null}
          <Badge variant="outline">Current data</Badge>
        </div>
        <div className="flex flex-wrap gap-2">
          <Link
            to="/query"
            search={{ env, sql }}
            className="inline-flex items-center gap-1.5 text-xs"
          >
            <ExternalLinkIcon className="size-3.5" /> Open in Query
          </Link>
          <Button
            variant="outline"
            size="sm"
            disabled={pending}
            onClick={() => void start(sql)}
          >
            <PlayIcon className="size-3.5" />
            {pending
              ? 'Loading…'
              : result
                ? 'Refresh rows'
                : 'Load failing rows'}
          </Button>
        </div>
      </div>
      <p className="m-0 text-xs leading-relaxed text-muted-foreground">
        Runs a read-only query in {env} using your dataset permissions. Results
        show the current table, which may differ from the rows at incident
        detection.
      </p>
      {error || session?.error ? (
        <p role="alert" className="text-sm text-bad-text">
          {error ?? session?.error}
        </p>
      ) : null}
      {pending ? (
        <p role="status" className="text-sm text-muted-foreground">
          Reading failing rows…
        </p>
      ) : null}
      <AuditRowsResult result={result} />
      {session?.status === 'completed' ? (
        <p className="m-0 break-all text-xs text-muted-foreground">
          Query {session.id} · ref {session.nessie_ref} · {session.updated_at}
        </p>
      ) : null}
    </section>
  )
}
