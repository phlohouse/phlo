import * as React from 'react'
import { getQueryHistory } from '@/lib/data/api/query'
import { Button } from '@/components/ui/button'

export function QueryHistory({ env }: { env: 'prod' | 'staging' }) {
  const [history, setHistory] =
    React.useState<Awaited<ReturnType<typeof getQueryHistory>>>()
  const [error, setError] = React.useState<string>()
  const [pending, setPending] = React.useState(false)
  async function refresh() {
    setPending(true)
    setError(undefined)
    try {
      setHistory(await getQueryHistory({ data: { env } }))
    } catch (caught) {
      setHistory(undefined)
      setError(
        caught instanceof Error ? caught.message : 'History unavailable.',
      )
    } finally {
      setPending(false)
    }
  }
  return (
    <section
      aria-label="Shared query history"
      className="mt-4 border-t border-line pt-3"
    >
      <h2 className="text-sm font-medium">Shared query history</h2>
      <p className="text-xs text-muted-foreground">
        Retained completed workspace executions in {env}. SQL and results remain
        private to their initiating actor.
      </p>
      <Button
        variant="outline"
        size="sm"
        disabled={pending}
        onClick={() => void refresh()}
      >
        {pending ? 'Loading history…' : 'Refresh history'}
      </Button>
      {error ? (
        <p role="alert" className="text-xs text-bad-ink">
          {error}
        </p>
      ) : null}
      {history?.status === 'unavailable' ? (
        <p role="status" className="text-xs">
          History unavailable. Durable query evidence is not configured.
        </p>
      ) : null}
      {history?.status === 'partial' ? (
        <>
          <p className="text-xs text-muted-foreground">
            Partial coverage: completed workspace executions only. Failed,
            cancelled and other engine executions are not listed.
          </p>
          {!history.items.length ? (
            <p className="text-xs">No retained completed executions.</p>
          ) : null}
          <ul className="space-y-3 p-0 text-xs">
            {history.items.map((item) => (
              <li key={item.id} className="list-none break-all">
                <span className="font-mono">{item.id}</span>
                <br />
                <time>{item.completed_at}</time> · {item.engine} ·{' '}
                {item.nessie_ref}
                <br />
                SQL SHA-256: <span className="font-mono">{item.sql_hash}</span>
              </li>
            ))}
          </ul>
          {history.truncated ? (
            <p className="text-xs">
              Showing the latest 50 executions. Older evidence is not displayed.
            </p>
          ) : null}
        </>
      ) : null}
    </section>
  )
}
