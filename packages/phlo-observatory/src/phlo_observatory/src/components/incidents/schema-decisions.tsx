import * as React from 'react'
import {
  getSchemaDecisions,
  incidentOperationKey,
  recordSchemaDecision,
} from '@/lib/data/api/incidents'
import { Button } from '@/components/ui/button'
import { Input, Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'

export function SchemaDecisions({
  env,
  incidentId,
  kind,
}: {
  env: 'prod' | 'staging'
  incidentId: string
  kind: string
}) {
  const [columns, setColumns] = React.useState<
    Array<{ name: string; side: 'source' | 'target' }>
  >([{ name: '', side: 'target' }])
  const [decisions, setDecisions] =
    React.useState<Awaited<ReturnType<typeof getSchemaDecisions>>>()
  const [error, setError] = React.useState<string>()
  const [pending, setPending] = React.useState(false)
  async function refresh() {
    setPending(true)
    setError(undefined)
    try {
      setDecisions(await getSchemaDecisions({ data: { env, id: incidentId } }))
    } catch (caught) {
      setDecisions(undefined)
      setError(
        caught instanceof Error ? caught.message : 'Decisions unavailable.',
      )
    } finally {
      setPending(false)
    }
  }
  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (pending) return
    const form = new FormData(event.currentTarget)
    const text = (key: string) => String(form.get(key) ?? '').trim()
    const names = columns.map((column) => column.name.trim())
    if (names.some((name) => !name) || new Set(names).size !== names.length) {
      setError('Each column needs a unique, non-empty name.')
      return
    }
    const decision = {
      source_ref: text('source_ref'),
      target_ref: text('target_ref'),
      source_hash: text('source_hash'),
      target_hash: text('target_hash'),
      table_key: text('table_key'),
      justification: text('justification'),
      columns: Object.fromEntries(
        columns.map((column) => [column.name.trim(), column.side]),
      ),
    }
    setPending(true)
    setError(undefined)
    try {
      await recordSchemaDecision({
        data: {
          env,
          id: incidentId,
          decision,
          idempotencyKey: incidentOperationKey(
            env,
            incidentId,
            'schema-decision',
            JSON.stringify(decision),
          ),
        },
      })
      setDecisions(await getSchemaDecisions({ data: { env, id: incidentId } }))
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Decision could not be recorded.',
      )
    } finally {
      setPending(false)
    }
  }
  if (!kind.toLowerCase().includes('schema')) return null
  return (
    <section
      aria-label="Per-column schema decisions"
      className="space-y-4 border-t border-line p-4 lg:p-7"
    >
      <h2 className="text-sm font-medium">Per-column schema decisions</h2>
      <p className="text-sm text-muted-foreground">
        Record the observed branch revisions, table and choice for every
        conflicting column. This records a decision, not a merge or
        code-contract change. Merge checks reject stale revisions and incomplete
        choices.
      </p>
      <Button
        variant="outline"
        disabled={pending}
        onClick={() => void refresh()}
      >
        Refresh recorded decisions
      </Button>
      {decisions ? (
        <div className="space-y-3 text-sm">
          {!decisions.items.length ? (
            <p>No recorded decisions.</p>
          ) : (
            decisions.items.map((decision) => (
              <article
                key={decision.id}
                className="break-all rounded border border-line p-3"
              >
                <p>
                  {decision.table_key} · {decision.actor} ·{' '}
                  <time>{decision.created_at}</time>
                </p>
                <p className="font-mono">
                  {decision.source_ref}@{decision.source_hash} →{' '}
                  {decision.target_ref}@{decision.target_hash}
                </p>
                <ul>
                  {Object.entries(decision.columns).map(([column, side]) => (
                    <li key={column}>
                      {column}: {side}
                    </li>
                  ))}
                </ul>
                <p>{decision.justification}</p>
              </article>
            ))
          )}
        </div>
      ) : null}
      <form onSubmit={(event) => void submit(event)} className="space-y-4">
        <fieldset disabled={pending} className="space-y-4">
          <legend className="text-sm font-medium">New decision</legend>
          <div className="grid gap-3 sm:grid-cols-2">
            {[
              ['source_ref', 'Source branch'],
              ['target_ref', 'Target branch'],
              ['source_hash', 'Observed source hash'],
              ['target_hash', 'Observed target hash'],
              ['table_key', 'Conflicting table key'],
            ].map(([name, label]) => (
              <label key={name} className="space-y-1 text-sm">
                {label}
                <Input
                  name={name}
                  required
                  maxLength={name === 'table_key' ? 512 : 256}
                />
              </label>
            ))}
          </div>
          {columns.map((column, index) => (
            <div key={index} className="flex flex-wrap items-end gap-2">
              <label className="min-w-0 basis-full text-sm sm:flex-1">
                Column {index + 1}
                <Input
                  value={column.name}
                  required
                  maxLength={512}
                  onChange={(event) =>
                    setColumns((items) =>
                      items.map((item, i) =>
                        i === index
                          ? { ...item, name: event.target.value }
                          : item,
                      ),
                    )
                  }
                />
              </label>
              <div className="w-32">
                <Select
                  aria-label={`Choice for column ${index + 1}`}
                  value={column.side}
                  onValueChange={(side: 'source' | 'target') =>
                    setColumns((items) =>
                      items.map((item, i) =>
                        i === index ? { ...item, side } : item,
                      ),
                    )
                  }
                  options={[
                    { value: 'source', label: 'Source' },
                    { value: 'target', label: 'Target' },
                  ]}
                />
              </div>
              <Button
                type="button"
                variant="outline"
                disabled={columns.length === 1}
                onClick={() =>
                  setColumns((items) => items.filter((_, i) => i !== index))
                }
              >
                Remove column {index + 1}
              </Button>
            </div>
          ))}
          <Button
            type="button"
            variant="outline"
            disabled={columns.length >= 500}
            onClick={() =>
              setColumns((items) => [...items, { name: '', side: 'target' }])
            }
          >
            Add column
          </Button>
          <label className="block text-sm">
            Justification
            <Textarea name="justification" required maxLength={4000} />
          </label>
          <Button type="submit">
            {pending ? 'Recording…' : 'Record decision'}
          </Button>
        </fieldset>
      </form>
      {error ? (
        <p role="alert" className="text-sm text-bad-ink">
          {error}
        </p>
      ) : null}
    </section>
  )
}
