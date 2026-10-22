/** Records schema choices from observed branches and revision-bound field differences. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { GitBranchIcon, RefreshCwIcon } from 'lucide-react'
import type {
  BranchSchemaComparison,
  getBranchDetail,
  getBranchesPage,
} from '@/lib/data/api/branches'
import {
  getBranchSchemaDiff,
  getBranchDetail as loadBranchDetail,
  getBranchesPage as loadBranches,
} from '@/lib/data/api/branches'
import {
  getSchemaDecisions,
  incidentOperationKey,
  recordSchemaDecision,
} from '@/lib/data/api/incidents'
import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Badge } from '@/components/ui/badge'

type Detail = Awaited<ReturnType<typeof getBranchDetail>>
type Refs = Awaited<ReturnType<typeof getBranchesPage>>
type Choice = 'source' | 'target'

function useSchemaContext(env: string, assetId?: string) {
  const [refs, setRefs] = React.useState<Refs>()
  const [source, setSource] = React.useState('')
  const [target, setTarget] = React.useState('')
  const [table, setTable] = React.useState('')
  const [detail, setDetail] = React.useState<Detail>()
  const [schema, setSchema] = React.useState<BranchSchemaComparison>()
  const [error, setError] = React.useState<string>()
  const [revision, setRevision] = React.useState(0)
  React.useEffect(() => {
    let active = true
    void loadBranches({ data: { env } })
      .then((value) => {
        if (active) {
          setRefs(value)
          setSource(
            value.branches.find((branch) => !branch.protected)?.name ?? '',
          )
          setTarget(
            value.branches.find((branch) => branch.protected)?.name ?? '',
          )
        }
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof Error ? caught.message : 'Branches unavailable.',
          )
      })
    return () => {
      active = false
    }
  }, [env])
  React.useEffect(() => {
    let active = true
    setDetail(undefined)
    setSchema(undefined)
    setError(undefined)
    setTable('')
    if (source && target && source !== target)
      void loadBranchDetail({ data: { env, branch: source, target } })
        .then((value) => {
          if (active) {
            setDetail(value)
            setTable(
              assetId
                ? (value.diff.items.find(
                    (item) => item.key === assetId.replaceAll('/', '.'),
                  )?.key ?? '')
                : (value.diff.items[0]?.key ?? ''),
            )
          }
        })
        .catch((caught: unknown) => {
          if (active)
            setError(
              caught instanceof Error
                ? caught.message
                : 'Comparison unavailable.',
            )
        })
    return () => {
      active = false
    }
  }, [env, source, target, revision, assetId])
  React.useEffect(() => {
    let active = true
    setSchema(undefined)
    setError(undefined)
    if (detail && table)
      void getBranchSchemaDiff({
        data: {
          env,
          branch: source,
          target,
          tableKey: table,
          sourceHash: detail.diff.source_hash,
          targetHash: detail.diff.target_hash,
        },
      })
        .then((value) => {
          if (active) setSchema(value)
        })
        .catch((caught: unknown) => {
          if (active)
            setError(
              caught instanceof Error
                ? caught.message
                : 'Schema differences unavailable.',
            )
        })
    return () => {
      active = false
    }
  }, [env, source, target, detail, table])
  return {
    refs,
    source,
    target,
    table,
    detail,
    schema,
    error,
    setSource,
    setTarget,
    setTable,
    refresh: () => setRevision((value) => value + 1),
  }
}

function ColumnChoice({
  column,
  choice,
  onChange,
}: {
  column: BranchSchemaComparison['columns'][number]
  choice?: Choice
  onChange: (side: Choice) => void
}) {
  return (
    <fieldset className="overflow-hidden rounded-lg border border-line">
      <legend className="sr-only">
        Choose the definition of {column.name}
      </legend>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line bg-raised px-3 py-2">
        <span className="font-mono text-sm">{column.name}</span>
        <Badge variant={choice ? 'ok' : 'warn'}>
          {choice ? 'Choice recorded in draft' : 'Decision needed'}
        </Badge>
      </div>
      <div className="grid sm:grid-cols-2">
        {(['target', 'source'] as const).map((side) => {
          const field = column[side]
          return (
            <label
              key={side}
              className={`flex cursor-pointer gap-3 border-b border-line px-3 py-4 last:border-0 sm:border-r sm:border-b-0 ${choice === side ? 'bg-primary-soft' : 'hover:bg-raised'}`}
            >
              <input
                type="radio"
                name={`column-${column.name}`}
                value={side}
                checked={choice === side}
                onChange={() => onChange(side)}
                className="mt-1 accent-primary"
              />
              <span className="min-w-0">
                <span className="mb-2 block text-xs text-muted-foreground">
                  Keep {side === 'target' ? 'target branch' : 'source branch'}
                </span>
                <span className="break-all font-mono text-xs">
                  {field ? `${field.name} ${field.type}` : 'Column absent'}
                </span>
                {field ? (
                  <span className="mt-1 block text-xs text-muted-foreground">
                    Field {field.field_id} ·{' '}
                    {field.required ? 'required' : 'nullable'}
                    {field.doc ? ` · ${field.doc}` : ''}
                  </span>
                ) : null}
              </span>
            </label>
          )
        })}
      </div>
    </fieldset>
  )
}

function SchemaBranchSelection({
  env,
  context,
}: {
  env: string
  context: ReturnType<typeof useSchemaContext>
}) {
  return (
    <>
      <div className="grid gap-3 sm:grid-cols-2">
        <label className="space-y-1 text-sm">
          Source branch
          <Select
            aria-label="Source branch"
            value={context.source}
            onValueChange={context.setSource}
            options={(context.refs?.branches ?? [])
              .filter((branch) => !branch.protected)
              .map((branch) => ({ value: branch.name, label: branch.name }))}
          />
        </label>
        <label className="space-y-1 text-sm">
          Target branch
          <Select
            aria-label="Target branch"
            value={context.target}
            onValueChange={context.setTarget}
            options={(context.refs?.branches ?? []).map((branch) => ({
              value: branch.name,
              label: branch.name,
            }))}
          />
        </label>
      </div>
      {!context.source && context.refs ? (
        <p className="rounded-lg border border-line p-4 text-sm">
          No working branch is visible in {env}.{' '}
          <Link to="/branches" search={{ env, dialog: 'new-branch' }}>
            Create a scoped branch
          </Link>{' '}
          to review a schema change.
        </p>
      ) : null}
      {context.detail ? (
        <label className="block space-y-1 text-sm">
          Changed table
          <Select
            aria-label="Changed table"
            value={context.table}
            onValueChange={context.setTable}
            options={context.detail.diff.items.map((item) => ({
              value: item.key,
              label: `${item.key} · ${item.status}`,
            }))}
          />
        </label>
      ) : null}
      {context.detail && !context.detail.diff.items.length ? (
        <p className="text-sm text-muted-foreground">
          No table differences were observed between these revisions.
        </p>
      ) : null}
    </>
  )
}

function SchemaDecisionWorkspace({
  env,
  incidentId,
  assetId,
}: {
  env: string
  incidentId: string
  assetId?: string
}) {
  const context = useSchemaContext(env, assetId)
  const [choices, setChoices] = React.useState<Record<string, Choice>>({})
  const [note, setNote] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const [decisions, setDecisions] =
    React.useState<Awaited<ReturnType<typeof getSchemaDecisions>>>()
  React.useEffect(() => {
    let active = true
    void getSchemaDecisions({ data: { env, id: incidentId } })
      .then((value) => {
        if (active) setDecisions(value)
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Recorded decisions unavailable.',
          )
      })
    return () => {
      active = false
    }
  }, [env, incidentId])
  React.useEffect(() => {
    setChoices({})
  }, [context.schema])
  const schema = context.schema
  const recorded = schema
    ? decisions?.items.find(
        (decision) =>
          decision.env === env &&
          decision.incident_id === incidentId &&
          decision.source_ref === schema.source &&
          decision.target_ref === schema.target &&
          decision.source_hash === schema.source_hash &&
          decision.target_hash === schema.target_hash &&
          decision.table_key === schema.table_key,
      )
    : undefined
  const complete =
    !recorded &&
    schema &&
    schema.columns.length > 0 &&
    schema.columns.every((column) => choices[column.name])
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (!schema || recorded || !complete || !note.trim() || pending) return
    setPending(true)
    setError(undefined)
    const decision = {
      source_ref: schema.source,
      target_ref: schema.target,
      source_hash: schema.source_hash,
      target_hash: schema.target_hash,
      table_key: schema.table_key,
      columns: choices,
      justification: note.trim(),
    }
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
  return (
    <section
      aria-label="Per-column schema decisions"
      className="space-y-4 border-t border-line pt-5"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 className="m-0 text-[15px] font-medium">
          {recorded ? 'Decision recorded' : 'Decision needed'}
        </h2>
        <Button
          variant="outline"
          size="sm"
          onClick={context.refresh}
          disabled={pending}
        >
          <RefreshCwIcon className="size-3.5" /> Refresh revisions
        </Button>
      </div>
      <p className="text-sm leading-relaxed text-muted-foreground">
        Compare the actual branch schemas and choose the definition for each
        changed column. The saved decision stays bound to both observed
        revisions.
      </p>
      <SchemaBranchSelection env={env} context={context} />
      {recorded ? (
        <article
          aria-label="Recorded decision for current revisions"
          className="space-y-3 rounded-lg border border-line bg-raised p-4 text-sm"
        >
          <Badge variant="ok">Recorded for these revisions</Badge>
          <p className="break-all font-mono">{recorded.table_key}</p>
          <ul className="space-y-1">
            {Object.entries(recorded.columns).map(([name, side]) => (
              <li key={name}>
                <span className="font-mono">{name}</span>: keep {side} branch
              </li>
            ))}
          </ul>
          <p>{recorded.justification}</p>
          <p className="text-xs text-muted-foreground">
            {recorded.actor} · {recorded.created_at}
          </p>
          <p className="break-all font-mono text-xs text-muted-foreground">
            {recorded.source_ref}@{recorded.source_hash.slice(0, 12)} →{' '}
            {recorded.target_ref}@{recorded.target_hash.slice(0, 12)}
          </p>
          <p className="text-xs text-muted-foreground">
            This immutable choice applies only to these pinned revisions.
            Checks, trial merge, signing and MFA remain required before merge.
          </p>
        </article>
      ) : schema ? (
        <SchemaChoiceForm
          schema={schema}
          choices={choices}
          onChoice={(name, side) =>
            setChoices((value) => ({ ...value, [name]: side }))
          }
          note={note}
          onNote={setNote}
          pending={pending}
          complete={!!complete}
          onSubmit={(event) => void submit(event)}
        />
      ) : null}
      {context.error || error ? (
        <p role="alert" className="text-sm text-bad-text">
          {context.error ?? error}
        </p>
      ) : null}
      {decisions ? (
        <details className="rounded-lg border border-line p-3">
          <summary className="cursor-pointer text-sm">
            Recorded decisions · {decisions.items.length}
          </summary>
          <div className="mt-3 space-y-3">
            {decisions.items.map((decision) => (
              <article
                key={decision.id}
                className="border-t border-line-soft pt-3 text-xs"
              >
                <p className="break-all font-mono">{decision.table_key}</p>
                <p className="text-muted-foreground">
                  {decision.actor} · {decision.created_at}
                </p>
                <p className="break-all font-mono text-muted-foreground">
                  {decision.source_ref}@{decision.source_hash.slice(0, 12)} →{' '}
                  {decision.target_ref}@{decision.target_hash.slice(0, 12)}
                </p>
                <ul>
                  {Object.entries(decision.columns).map(([name, side]) => (
                    <li key={name}>
                      {name}: {side}
                    </li>
                  ))}
                </ul>
                <p>{decision.justification}</p>
              </article>
            ))}
          </div>
        </details>
      ) : null}
      <Link
        to="/branches"
        search={{ env, branch: context.source || undefined }}
        className="inline-flex items-center gap-2 text-sm"
      >
        <GitBranchIcon className="size-4" /> Review checks and merge in Branches
      </Link>
    </section>
  )
}

function SchemaChoiceForm({
  schema,
  choices,
  onChoice,
  note,
  onNote,
  pending,
  complete,
  onSubmit,
}: {
  schema: BranchSchemaComparison
  choices: Record<string, Choice>
  onChoice: (name: string, side: Choice) => void
  note: string
  onNote: (value: string) => void
  pending: boolean
  complete: boolean
  onSubmit: (event: React.FormEvent) => void
}) {
  return (
    <form onSubmit={onSubmit} className="space-y-4">
      <fieldset disabled={pending} className="space-y-4">
        <legend className="sr-only">
          Schema choices for {schema.table_key}
        </legend>
        <details className="rounded-lg border border-line px-3 py-2 text-xs text-muted-foreground">
          <summary className="cursor-pointer">Observed revisions</summary>
          <p className="break-all font-mono">
            {schema.source}@{schema.source_hash}
          </p>
          <p className="break-all font-mono">
            {schema.target}@{schema.target_hash}
          </p>
        </details>
        {schema.columns.length ? (
          schema.columns.map((column) => (
            <ColumnChoice
              key={column.name}
              column={column}
              choice={choices[column.name]}
              onChange={(side) => onChoice(column.name, side)}
            />
          ))
        ) : (
          <p className="text-sm text-muted-foreground">
            The pinned table schemas have no differing primitive fields.
          </p>
        )}
        <label className="block space-y-2 text-sm">
          Note for the audit log
          <Textarea
            required
            maxLength={4000}
            rows={3}
            value={note}
            onChange={(event) => onNote(event.target.value)}
          />
        </label>
        <Button type="submit" disabled={!complete || !note.trim() || pending}>
          {pending ? 'Recording…' : 'Record schema decision'}
        </Button>
        <p className="m-0 text-xs leading-relaxed text-muted-foreground">
          This saves column choices for the observed revisions. Trial merge,
          branch checks, signing and MFA remain required before merge.
        </p>
      </fieldset>
    </form>
  )
}

export function SchemaDecisions({
  env,
  incidentId,
  kind,
  assetId,
}: {
  env: string
  incidentId: string
  kind: string
  assetId?: string
}) {
  return /schema|merge/.test(kind.toLowerCase()) ? (
    <SchemaDecisionWorkspace
      key={`${env}:${incidentId}`}
      env={env}
      incidentId={incidentId}
      assetId={assetId}
    />
  ) : null
}
