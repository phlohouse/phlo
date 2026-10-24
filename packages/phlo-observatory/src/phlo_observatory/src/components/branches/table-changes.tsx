/** Expands real table changes into schema differences pinned to observed branch heads. */
import * as React from 'react'
import { ChevronDownIcon } from 'lucide-react'
import type {
  BranchDiff,
  BranchSchemaComparison,
} from '@/lib/data/api/branches'
import { getBranchSchemaDiff } from '@/lib/data/api/branches'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'

function TableChange({
  env,
  diff,
  change,
}: {
  env: string
  diff: BranchDiff
  change: BranchDiff['items'][number]
}) {
  const [schema, setSchema] = React.useState<BranchSchemaComparison>()
  const [error, setError] = React.useState<string>()
  const [pending, setPending] = React.useState(false)
  async function load() {
    setPending(true)
    setError(undefined)
    try {
      setSchema(
        await getBranchSchemaDiff({
          data: {
            env,
            branch: diff.source,
            target: diff.target,
            tableKey: change.key,
            sourceHash: diff.source_hash,
            targetHash: diff.target_hash,
          },
        }),
      )
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'Schema comparison unavailable.',
      )
    } finally {
      setPending(false)
    }
  }
  return (
    <li className="overflow-hidden rounded-lg border border-line">
      <div className="flex flex-wrap items-center justify-between gap-2 bg-raised px-3 py-3">
        <span className="min-w-0 break-all font-mono text-xs">
          {change.key}
        </span>
        <Badge variant="outline">{change.status}</Badge>
      </div>
      <div className="space-y-3 p-3">
        <Button
          variant="ghost"
          size="sm"
          disabled={pending}
          onClick={() => void load()}
        >
          <ChevronDownIcon className="size-3.5" />
          {pending
            ? 'Loading schemas…'
            : schema
              ? 'Refresh column comparison'
              : 'Compare columns'}
        </Button>
        {schema ? (
          <div className="overflow-x-auto">
            <table className="w-full text-left text-xs">
              <caption className="sr-only">
                Observed column changes for {change.key}
              </caption>
              <thead>
                <tr className="text-muted-foreground">
                  <th className="px-2 py-2 font-normal">On {diff.target}</th>
                  <th className="px-2 py-2 font-normal">On {diff.source}</th>
                </tr>
              </thead>
              <tbody>
                {schema.columns.map((column) => (
                  <tr key={column.name}>
                    <td className="border border-line bg-bad-soft px-2 py-2 font-mono text-bad-text">
                      {column.target
                        ? `- ${column.target.name} ${column.target.type}`
                        : 'Absent'}
                    </td>
                    <td className="border border-line bg-ok-wash px-2 py-2 font-mono text-ok-text">
                      {column.source
                        ? `+ ${column.source.name} ${column.source.type}`
                        : 'Absent'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
            {!schema.columns.length ? (
              <p className="text-xs text-muted-foreground">
                No primitive column changes. Table content changed without a
                field-definition difference.
              </p>
            ) : null}
          </div>
        ) : null}
        {error ? (
          <p role="alert" className="m-0 text-xs text-bad-text">
            {error}
          </p>
        ) : null}
      </div>
    </li>
  )
}

export function BranchTableChanges({
  env,
  diff,
}: {
  env: string
  diff: BranchDiff
}) {
  return (
    <section className="space-y-3">
      <h3 className="m-0 text-xs font-medium tracking-wide text-muted-foreground uppercase">
        Table changes{diff.truncated ? ' · first 500' : ''}
      </h3>
      {diff.items.length ? (
        <ul className="m-0 list-none space-y-3 p-0">
          {diff.items.map((change) => (
            <TableChange
              key={`${change.key}:${diff.source_hash}:${diff.target_hash}`}
              env={env}
              diff={diff}
              change={change}
            />
          ))}
        </ul>
      ) : (
        <p className="text-sm text-muted-foreground">
          No table changes against {diff.target}.
        </p>
      )}
    </section>
  )
}
