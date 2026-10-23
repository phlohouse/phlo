/** Runs generated read-only metric SQL through the existing Query permission boundary. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { MetricModel } from '@/lib/data/api/metrics'
import type { QuerySession } from '@/lib/data/api/query'
import { getMetricPreviewSql } from '@/lib/data/api/metrics'
import { getQuerySession, submitQuery } from '@/lib/data/api/query'
import { ResultsGrid } from '@/components/query/result-views'
import { Button } from '@/components/ui/button'
import { useActionPermission } from '@/components/phlo/action-availability'

export function MetricModelPreview({
  model,
  env,
}: {
  model: MetricModel
  env: string
}) {
  const [groups, setGroups] = React.useState<Array<string>>([])
  const [sql, setSql] = React.useState('')
  const [session, setSession] = React.useState<QuerySession>()
  const [error, setError] = React.useState<string>()
  const [busy, setBusy] = React.useState(false)
  const queryPermission = useActionPermission('v1_query_submit')
  const active = React.useRef(true)
  React.useEffect(() => {
    active.current = true
    return () => {
      active.current = false
    }
  }, [])
  const preview = async () => {
    setBusy(true)
    setError(undefined)
    setSession(undefined)
    setSql('')
    try {
      const generated = await getMetricPreviewSql({
        data: { env, id: model.model_id, group_by: groups },
      })
      if (!active.current) return
      if (generated.version !== model.version)
        throw new Error('The model changed. Reload it before previewing.')
      setSql(generated.sql)
      let next = await submitQuery({ data: { env, sql: generated.sql } })
      while (
        active.current &&
        !['completed', 'failed', 'cancelled'].includes(next.status)
      ) {
        setSession(next)
        await new Promise((resolve) => window.setTimeout(resolve, 750))
        if (!active.current) return
        next = await getQuerySession({ data: { env, id: next.id } })
      }
      if (!active.current) return
      setSession(next)
      if (next.error) setError(next.error)
    } catch (caught) {
      if (active.current)
        setError(
          caught instanceof Error ? caught.message : 'Metric preview failed.',
        )
    } finally {
      if (active.current) setBusy(false)
    }
  }
  return (
    <aside
      aria-label="Metric preview"
      className="flex min-w-0 shrink-0 flex-col gap-4 border-t border-line p-5 xl:w-[360px] xl:border-t-0 xl:border-l"
    >
      <h2 className="m-0 text-sm font-medium">Try this model</h2>
      <p className="m-0 text-xs text-muted-foreground">
        Runs the saved version against current data on {model.nessie_ref}.
        Requires Query preview and dataset query permission.
      </p>
      <fieldset className="grid gap-2 border-0 p-0">
        <legend className="mb-2 text-xs text-muted-foreground">Group by</legend>
        {model.dimensions.map((dimension) => (
          <label
            key={dimension.name}
            className="flex items-center gap-2 text-[13px]"
          >
            <input
              type="checkbox"
              checked={groups.includes(dimension.name)}
              onChange={(event) =>
                setGroups((items) =>
                  event.target.checked
                    ? [...items, dimension.name]
                    : items.filter((item) => item !== dimension.name),
                )
              }
              className="accent-primary"
            />
            {dimension.name}
            <span className="ml-auto text-xs text-muted-foreground">
              {dimension.kind}
            </span>
          </label>
        ))}
      </fieldset>
      <Button
        operation="v1_metric_model_preview"
        onClick={() => void preview()}
        disabled={
          busy ||
          model.source_status === 'unavailable' ||
          !queryPermission.allowed
        }
      >
        {busy ? `Query ${session?.status ?? 'starting'}…` : 'Run preview'}
      </Button>
      {model.source_status === 'unavailable' ? (
        <p role="status" className="m-0 text-sm text-warn-text">
          {model.source_reason ??
            'The saved source is unavailable. Edit the draft to select an available source before previewing.'}
        </p>
      ) : null}
      {!queryPermission.allowed ? (
        <p className="m-0 text-xs text-muted-foreground">
          {queryPermission.reason}
        </p>
      ) : null}
      {error ? (
        <p role="alert" className="m-0 text-sm text-bad-text">
          {error}
        </p>
      ) : null}
      {session?.status === 'completed' && session.result ? (
        <div className="flex max-h-[320px] min-h-[150px] flex-col overflow-hidden rounded-lg border border-line">
          <p
            role="status"
            className="m-0 border-b border-line px-3 py-2 text-xs text-ok-text"
          >
            {session.result.rows.length} returned rows
            {session.result.has_more ? ' · truncated' : ''}
          </p>
          <ResultsGrid result={session.result} />
        </div>
      ) : null}
      {sql ? (
        <>
          <details>
            <summary className="cursor-pointer text-xs text-link">
              Show SQL
            </summary>
            <pre className="max-h-72 overflow-auto rounded-lg bg-background p-3 font-mono text-xs whitespace-pre-wrap">
              {sql}
            </pre>
          </details>
          <Button
            variant="outline"
            render={<Link to="/query" search={{ env, sql }} />}
          >
            Open in Query
          </Button>
        </>
      ) : null}
      <p className="m-0 border-t border-line pt-4 text-xs text-muted-foreground">
        Draft preview is not a verified question or a published semantic model.
        Definitions do not change data or access.
      </p>
    </aside>
  )
}
