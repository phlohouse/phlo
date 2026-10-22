/** Full-file evidence supports explicit date and qualifier choices, never silent coercion. */
import { AlertTriangleIcon } from 'lucide-react'
import type { CsvUpload, UploadColumn } from '@/lib/data/api/uploads'
import { Button } from '@/components/ui/button'

type ColumnProfile = NonNullable<CsvUpload['column_assistance']>[number]
type AssistanceProps = {
  upload: CsvUpload
  columns: Array<UploadColumn>
  busy: boolean
  onChange: (index: number, patch: Partial<UploadColumn>) => void
}

export function CsvColumnAssistance({
  upload,
  columns,
  busy,
  onChange,
}: AssistanceProps) {
  const profiles = upload.column_assistance ?? []
  if (!profiles.length) return null
  return (
    <section aria-label="Detected column formats" className="min-w-0 space-y-2">
      <h3 className="text-sm font-medium">
        {profiles.length} column {profiles.length === 1 ? 'format' : 'formats'}{' '}
        to review
      </h3>
      <p className="text-xs text-muted-foreground">
        Detected across the full file. You choose; validate all rows to save
        these choices.
      </p>
      {profiles.map((profile) => {
        const index = columns.findIndex(
          (column) => column.column === profile.column,
        )
        const column = columns[index]
        if (!column) return null
        return (
          <div key={profile.column} className="space-y-2">
            {profile.dates ? (
              <DateAssistance
                profile={profile}
                column={column}
                busy={busy}
                onChange={(patch) => onChange(index, patch)}
              />
            ) : null}
            {profile.qualifiers ? (
              <QualifierAssistance
                profile={profile}
                column={column}
                columns={columns}
                busy={busy}
                onChange={(patch) => onChange(index, patch)}
              />
            ) : null}
          </div>
        )
      })}
    </section>
  )
}
function DateAssistance({
  profile,
  column,
  busy,
  onChange,
}: {
  profile: ColumnProfile
  column: UploadColumn
  busy: boolean
  onChange: (patch: Partial<UploadColumn>) => void
}) {
  const dates = profile.dates!
  const selected = column.type === 'date' ? column.date_format : null
  return (
    <div className="min-w-0 space-y-3 rounded-xl border border-warn-line bg-warn-wash p-3 text-warn-ink">
      <p className="flex items-start gap-2 text-sm">
        <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
        <span>
          <code>{profile.column}</code> contains{' '}
          {dates.day_first.toLocaleString()} day-first,{' '}
          {dates.month_first.toLocaleString()} month-first and{' '}
          {dates.iso.toLocaleString()} ISO dates.
          {dates.ambiguous
            ? ` ${dates.ambiguous.toLocaleString()} dates are ambiguous between UK and US order.`
            : ''}
          {dates.invalid
            ? ` ${dates.invalid.toLocaleString()} values are not recognized dates.`
            : ''}
        </span>
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={selected === 'day_first' ? 'default' : 'outline'}
          aria-pressed={selected === 'day_first'}
          disabled={busy}
          onClick={() =>
            onChange({
              type: 'date',
              date_format: 'day_first',
              qualifiers: null,
            })
          }
        >
          Day first (UK)
        </Button>
        <Button
          size="sm"
          variant={selected === 'month_first' ? 'default' : 'outline'}
          aria-pressed={selected === 'month_first'}
          disabled={busy}
          onClick={() =>
            onChange({
              type: 'date',
              date_format: 'month_first',
              qualifiers: null,
            })
          }
        >
          Month first (US)
        </Button>
        <Button
          size="sm"
          variant={selected === 'iso' ? 'default' : 'outline'}
          aria-pressed={selected === 'iso'}
          disabled={busy}
          onClick={() =>
            onChange({ type: 'date', date_format: 'iso', qualifiers: null })
          }
        >
          ISO only
        </Button>
      </div>
      <p className="text-xs">
        {selected
          ? 'Proposed conversion; saved preview updates after validation.'
          : 'Choose a date order to preview the normalized values. Original file text is retained.'}
      </p>
      {selected ? (
        <ul className="flex flex-wrap gap-x-5 gap-y-1 font-mono text-xs">
          {dates.examples.map((example) => (
            <li key={example.original}>
              {example.original} →{' '}
              {example[selected] ?? 'does not match chosen order'}
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  )
}
function derivedNames(column: UploadColumn, columns: Array<UploadColumn>) {
  const used = new Set(
    columns.flatMap((item) => [
      item.name,
      ...(item.qualifiers
        ? [item.qualifiers.value_name, item.qualifiers.flag_name]
        : []),
    ]),
  )
  function available(suffix: string) {
    let index = 0
    let name = ''
    do {
      name = `${column.name.slice(0, 50)}${index ? `_${index}` : ''}_${suffix}`
      index++
    } while (used.has(name))
    used.add(name)
    return name
  }
  return { value_name: available('value'), flag_name: available('flag') }
}
function QualifierAssistance({
  profile,
  column,
  columns,
  busy,
  onChange,
}: {
  profile: ColumnProfile
  column: UploadColumn
  columns: Array<UploadColumn>
  busy: boolean
  onChange: (patch: Partial<UploadColumn>) => void
}) {
  const found = profile.qualifiers!
  const suggested = column.qualifiers ?? derivedNames(column, columns)
  return (
    <div className="min-w-0 space-y-3 rounded-xl border border-warn-line bg-warn-wash p-3 text-warn-ink">
      <p className="flex items-start gap-2 text-sm">
        <AlertTriangleIcon className="mt-0.5 size-4 shrink-0" />
        <span>
          <code>{profile.column}</code> has {found.numbers.toLocaleString()}{' '}
          numeric values and {(found.nd + found.below_loq).toLocaleString()}{' '}
          rows marked ND or &lt;LOQ.
          {found.other
            ? ` ${found.other} other text values need review before splitting.`
            : ''}
        </span>
      </p>
      <div className="flex flex-wrap gap-2">
        <Button
          size="sm"
          variant={column.qualifiers ? 'default' : 'outline'}
          className="h-auto max-w-full text-left whitespace-normal"
          aria-pressed={Boolean(column.qualifiers)}
          disabled={busy}
          onClick={() => onChange({ type: 'string', qualifiers: suggested })}
        >
          Keep text, add {suggested.value_name} and {suggested.flag_name}
        </Button>
        <Button
          size="sm"
          variant={
            !column.qualifiers && column.type === 'string'
              ? 'default'
              : 'outline'
          }
          aria-pressed={!column.qualifiers && column.type === 'string'}
          disabled={busy}
          onClick={() => onChange({ type: 'string', qualifiers: null })}
        >
          Keep as text only
        </Button>
      </div>
      {column.qualifiers ? (
        <>
          <p className="text-xs">
            Numbers produce a numeric value and no flag. ND and &lt;LOQ produce
            a null value and a flag. The original text stays in{' '}
            <code>{column.name}</code>; other text raises a validation error.
          </p>
          <details className="min-w-0">
            <summary className="cursor-pointer text-xs font-medium">
              Edit derived column names
            </summary>
            <div className="mt-2 grid min-w-0 gap-2 sm:grid-cols-2">
              <label className="min-w-0 space-y-1 text-xs">
                Numeric column
                <input
                  aria-label={`Numeric column for ${profile.column}`}
                  value={column.qualifiers.value_name}
                  disabled={busy}
                  onChange={(event) =>
                    onChange({
                      qualifiers: {
                        ...column.qualifiers!,
                        value_name: event.target.value,
                      },
                    })
                  }
                  className="block w-full min-w-0 rounded-md border border-warn-line bg-background px-2 py-1.5 font-mono"
                />
              </label>
              <label className="min-w-0 space-y-1 text-xs">
                Flag column
                <input
                  aria-label={`Flag column for ${profile.column}`}
                  value={column.qualifiers.flag_name}
                  disabled={busy}
                  onChange={(event) =>
                    onChange({
                      qualifiers: {
                        ...column.qualifiers!,
                        flag_name: event.target.value,
                      },
                    })
                  }
                  className="block w-full min-w-0 rounded-md border border-warn-line bg-background px-2 py-1.5 font-mono"
                />
              </label>
            </div>
          </details>
        </>
      ) : null}
    </div>
  )
}
