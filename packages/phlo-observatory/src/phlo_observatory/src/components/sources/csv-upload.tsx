/** Retained-file inspection followed by a reviewed, separate candidate load. */
import * as React from 'react'
import { CheckCircle2Icon, FileSpreadsheetIcon, UploadIcon } from 'lucide-react'
import type { CsvUpload, UploadColumn } from '@/lib/data/api/uploads'
import {
  getCsvUpload,
  retainCsvUpload,
  reviewCsvUpload,
} from '@/lib/data/api/uploads'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { CsvDestination } from '@/components/sources/csv-destination'
import { CsvColumnAssistance } from '@/components/sources/csv-column-assistance'

export function CsvUploadReview({
  env,
  initial,
  onRetained,
}: {
  env: string
  initial: CsvUpload | null
  onRetained: (id: string) => void
}) {
  const [upload, setUpload] = React.useState(initial)
  const [showFilePicker, setShowFilePicker] = React.useState(!initial)
  const filePickerId = React.useId()
  const [columns, setColumns] = React.useState<Array<UploadColumn>>(
    initial?.columns ?? [],
  )
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const dirty =
    JSON.stringify(columns) !== JSON.stringify(upload?.columns ?? [])
  function accept(result: CsvUpload) {
    setUpload(result)
    setColumns(result.columns)
    setError('')
  }
  async function retain(form: FormData) {
    setBusy(true)
    setError('')
    try {
      form.set('env', env)
      const result = await retainCsvUpload({ data: form })
      accept(result)
      setShowFilePicker(false)
      onRetained(result.upload_id)
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'The file could not be retained.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function validate() {
    if (!upload) return
    setBusy(true)
    setError('')
    try {
      accept(
        await reviewCsvUpload({
          data: {
            env,
            upload_id: upload.upload_id,
            expected_version: upload.version,
            columns,
          },
        }),
      )
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Column review failed. Reload the retained review before retrying.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function reload() {
    if (!upload) return
    setBusy(true)
    setError('')
    try {
      accept(await getCsvUpload({ data: { env, upload_id: upload.upload_id } }))
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Retained file verification failed.',
      )
    } finally {
      setBusy(false)
    }
  }
  function update(index: number, patch: Partial<UploadColumn>) {
    setColumns((previous) =>
      previous.map((column, position) =>
        position === index ? { ...column, ...patch } : column,
      ),
    )
  }
  return (
    <div className="grid min-h-0 min-w-0 w-full max-w-full flex-1 grid-cols-[minmax(0,1fr)] overflow-y-auto lg:grid-cols-[minmax(0,1fr)_320px]">
      <div className="min-w-0 max-w-full space-y-5 p-5">
        <div className="flex flex-wrap items-center gap-3 text-[13px]">
          <span className="flex items-center gap-1.5">
            {upload ? (
              <CheckCircle2Icon className="size-4 text-ok" />
            ) : (
              <UploadIcon className="size-4" />
            )}
            1 File
          </span>
          <span className={upload ? 'font-medium' : 'text-muted-foreground'}>
            2 Check columns
          </span>
          <span className="text-muted-foreground">3 Destination</span>
          <span className="text-muted-foreground">4 Review and load</span>
        </div>
        {upload ? (
          <UploadSummary
            upload={upload}
            dirty={dirty}
            busy={busy}
            expanded={showFilePicker}
            pickerId={filePickerId}
            onReplace={() => setShowFilePicker((value) => !value)}
          />
        ) : null}
        {showFilePicker ? (
          <RetainCsvForm
            upload={upload}
            busy={busy}
            id={filePickerId}
            onRetain={retain}
          />
        ) : null}
        {error ? (
          <p
            role="alert"
            className="rounded-lg border border-bad-line bg-bad-soft p-3 text-sm text-bad-ink"
          >
            {error}
          </p>
        ) : null}
        {upload ? (
          <>
            {upload.parse_errors.length ? (
              <div
                role="alert"
                className="space-y-2 rounded-lg border border-warn-line bg-warn-wash p-3 text-sm text-warn-ink"
              >
                {upload.parse_errors.map((message) => (
                  <p key={message}>{message}</p>
                ))}
                <p>
                  Replace the file to continue. Its original bytes remain
                  retained.
                </p>
              </div>
            ) : (
              <>
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <h2 className="text-sm font-medium">Check columns</h2>
                    <p className="mt-1 text-xs text-muted-foreground">
                      Start with the original text. Choose types explicitly;
                      validation checks every row.
                    </p>
                  </div>
                  <Button
                    operation="v1_csv_upload_review"
                    disabled={busy}
                    onClick={() => void validate()}
                  >
                    Validate all {upload.row_count?.toLocaleString()} rows
                  </Button>
                </div>
                {dirty ? (
                  <p role="status" className="text-xs text-warn-ink">
                    Unsaved column choices. The preview shows the saved review
                    until you validate all rows.
                  </p>
                ) : null}
                <CsvColumnAssistance
                  upload={upload}
                  columns={columns}
                  busy={busy}
                  onChange={update}
                />
                <details className="min-w-0 max-w-full rounded-lg border border-line">
                  <summary className="cursor-pointer px-3 py-2 text-xs font-medium hover:bg-raised">
                    Edit all columns
                  </summary>
                  <fieldset
                    disabled={busy}
                    className="min-w-0 max-w-full overflow-x-auto rounded-xl border border-line"
                  >
                    <legend className="sr-only">Column choices</legend>
                    <table className="w-full text-left text-[13px]">
                      <thead className="bg-raised text-xs text-muted-foreground">
                        <tr>
                          <th className="p-2.5 font-normal">Original column</th>
                          <th className="p-2.5 font-normal">
                            Name as reviewed
                          </th>
                          <th className="p-2.5 font-normal">Type</th>
                          <th className="p-2.5 font-normal">Nullable</th>
                          <th className="p-2.5 font-normal">Date format</th>
                        </tr>
                      </thead>
                      <tbody>
                        {columns.map((column, index) => (
                          <tr
                            key={column.column}
                            className="border-t border-line"
                          >
                            <td className="p-2.5 font-mono text-xs">
                              {column.column}
                            </td>
                            <td className="p-2.5">
                              <input
                                aria-label={`Name for ${column.column}`}
                                value={column.name}
                                onChange={(event) =>
                                  update(index, { name: event.target.value })
                                }
                                className="h-8 w-40 rounded-md border border-border bg-card px-2 font-mono text-xs"
                              />
                            </td>
                            <td className="p-2.5">
                              <select
                                aria-label={`Type for ${column.column}`}
                                value={column.type}
                                onChange={(event) =>
                                  update(index, {
                                    type: event.target
                                      .value as UploadColumn['type'],
                                    qualifiers:
                                      event.target.value === 'string'
                                        ? column.qualifiers
                                        : null,
                                  })
                                }
                                className="h-8 rounded-md border border-border bg-card px-2"
                              >
                                {[
                                  'string',
                                  'integer',
                                  'number',
                                  'boolean',
                                  'date',
                                ].map((type) => (
                                  <option key={type} value={type}>
                                    {type === 'string' ? 'Keep as text' : type}
                                  </option>
                                ))}
                              </select>
                            </td>
                            <td className="p-2.5">
                              <input
                                type="checkbox"
                                aria-label={`Nullable ${column.column}`}
                                checked={column.nullable}
                                onChange={(event) =>
                                  update(index, {
                                    nullable: event.target.checked,
                                  })
                                }
                              />
                            </td>
                            <td className="p-2.5">
                              <select
                                aria-label={`Date format for ${column.column}`}
                                disabled={column.type !== 'date'}
                                value={column.date_format}
                                onChange={(event) =>
                                  update(index, {
                                    date_format: event.target
                                      .value as UploadColumn['date_format'],
                                  })
                                }
                                className="h-8 rounded-md border border-border bg-card px-2 disabled:opacity-40"
                              >
                                <option value="iso">ISO · YYYY-MM-DD</option>
                                <option value="day_first">
                                  Day first · UK + ISO
                                </option>
                                <option value="month_first">
                                  Month first · US + ISO
                                </option>
                              </select>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </fieldset>
                </details>
                <UploadValidationErrors upload={upload} />
                <UploadPreview upload={upload} />
                <CsvDestination env={env} upload={upload} dirty={dirty} />
              </>
            )}
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => void reload()}
            >
              Reload retained review
            </Button>
          </>
        ) : null}
      </div>
      <UploadSidebar upload={upload} />
    </div>
  )
}
function UploadPreview({ upload }: { upload: CsvUpload }) {
  const output =
    upload.output_columns ??
    upload.columns.map((column) => ({
      name: column.name,
      type: column.type,
      source_column: column.column,
      derived: false,
      nullable: column.nullable,
    }))
  return (
    <section className="min-w-0 max-w-full" aria-labelledby="csv-preview-title">
      <h2 id="csv-preview-title" className="mb-2 text-sm font-medium">
        First rows · saved review
      </h2>
      <p className="mb-2 text-xs text-muted-foreground">
        Up to {upload.sample_limit} rows. Invalid values show their original
        text; full-file validation errors are listed above.
      </p>
      <div className="max-h-96 min-w-0 max-w-full overflow-auto rounded-xl border border-line">
        <table className="w-full text-left font-mono text-xs">
          <thead className="sticky top-0 bg-raised">
            <tr>
              {output.map((column) => (
                <th
                  key={column.name}
                  className={`min-w-28 p-3 font-normal ${column.derived ? 'bg-info-soft text-info' : 'text-muted-foreground'}`}
                >
                  {column.name}
                  <span className="ml-2 text-[10px]">{column.type}</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {upload.raw_rows.map((raw, index) => (
              <tr key={index} className="border-t border-line">
                {output.map((column) => {
                  const issue = upload.errors.find(
                    (item) =>
                      item.row === index + 2 &&
                      item.column === column.source_column,
                  )
                  const value = issue
                    ? column.derived
                      ? null
                      : raw[column.source_column]
                    : upload.rows[index]?.[column.name]
                  return (
                    <td
                      key={column.name}
                      title={
                        issue?.message ??
                        `Original: ${raw[column.source_column]}`
                      }
                      className={`px-3 py-2.5 whitespace-nowrap ${issue ? 'text-warn-ink' : ''} ${column.derived ? 'bg-info-soft/30' : ''}`}
                    >
                      {value == null ? '—' : String(value)}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function UploadSidebar({ upload }: { upload: CsvUpload | null }) {
  return (
    <aside className="space-y-4 border-t border-line bg-raised p-5 text-[13px] lg:border-t-0 lg:border-l">
      <h2 className="text-[15px] font-medium">Where it goes</h2>
      <p className="text-muted-foreground">
        The original file is retained as your private draft. After column
        review, choose a new bronze destination and confirm a separate candidate
        load. The base reference changes only through a separate reviewed merge.
      </p>
      {upload ? (
        <>
          <dl className="space-y-3 border-t border-line pt-4">
            <div>
              <dt className="text-muted-foreground">Retained by</dt>
              <dd className="mt-1 break-all">{upload.actor}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Environment</dt>
              <dd>{upload.env}</dd>
            </div>
            <div>
              <dt className="text-muted-foreground">
                SHA-256 of original bytes
              </dt>
              <dd className="mt-1 break-all font-mono text-[11px]">
                {upload.checksum}
              </dd>
            </div>
            <div>
              <dt className="text-muted-foreground">Review</dt>
              <dd>
                {upload.reviewed_by
                  ? `Revision ${upload.version} by ${upload.reviewed_by}`
                  : 'Not yet reviewed'}
              </dd>
            </div>
          </dl>
          <p className="rounded-lg bg-sunken p-3 text-xs leading-relaxed text-muted-foreground">
            Every reload and review verifies the retained checksum. Original
            text and file bytes remain available alongside the reviewed preview.
          </p>
        </>
      ) : null}
    </aside>
  )
}
function UploadSummary({
  upload,
  dirty,
  busy,
  expanded,
  pickerId,
  onReplace,
}: {
  upload: CsvUpload
  dirty: boolean
  busy: boolean
  expanded: boolean
  pickerId: string
  onReplace: () => void
}) {
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-3 rounded-xl border border-line p-3">
      <FileSpreadsheetIcon className="size-5 shrink-0 text-muted-foreground" />
      <div className="min-w-0 flex-1 basis-48">
        <h2 className="break-all font-mono text-xs font-medium">
          {upload.filename}
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          {upload.byte_count.toLocaleString()} bytes ·{' '}
          {upload.row_count?.toLocaleString() ?? 'Unparsed'} rows ·{' '}
          {upload.raw_columns.length} columns · revision {upload.version}
        </p>
      </div>
      <Badge
        variant={
          upload.status === 'reviewed'
            ? 'ok'
            : upload.status === 'invalid'
              ? 'warn'
              : 'neutral'
        }
      >
        {dirty
          ? 'Unsaved column choices'
          : upload.status === 'reviewed'
            ? 'Columns validated'
            : upload.status === 'invalid'
              ? 'Needs correction'
              : 'Retained · review needed'}
      </Badge>
      <Button
        size="sm"
        variant="outline"
        disabled={busy}
        aria-expanded={expanded}
        aria-controls={pickerId}
        onClick={onReplace}
      >
        {expanded ? 'Cancel replacement' : 'Replace file'}
      </Button>
    </div>
  )
}

function UploadValidationErrors({ upload }: { upload: CsvUpload }) {
  return upload.error_count ? (
    <div
      role="alert"
      className="rounded-lg border border-warn-line bg-warn-wash p-3 text-sm text-warn-ink"
    >
      <p>
        {upload.error_count.toLocaleString()} value errors across the complete
        file. Correct the choices or keep mixed values as text before preparing
        a candidate load.
      </p>
      <ul className="mt-2 max-h-40 list-disc space-y-1 overflow-y-auto pl-4 text-xs">
        {upload.errors.map((item, index) => (
          <li key={index}>
            Record {item.row} · {item.column}: {item.message}
          </li>
        ))}
      </ul>
      {upload.error_count > upload.errors.length ? (
        <p className="mt-2 text-xs">
          Showing the first {upload.errors.length} errors.
        </p>
      ) : null}
    </div>
  ) : null
}

function RetainCsvForm({
  upload,
  busy,
  id,
  onRetain,
}: {
  upload: CsvUpload | null
  busy: boolean
  id: string
  onRetain: (form: FormData) => Promise<void>
}) {
  return (
    <form
      id={id}
      className="flex flex-wrap items-center gap-3 rounded-xl border border-line p-4"
      onSubmit={(event) => {
        event.preventDefault()
        void onRetain(new FormData(event.currentTarget))
      }}
    >
      <FileSpreadsheetIcon className="size-5 text-muted-foreground" />
      <label className="flex min-w-0 flex-1 flex-col gap-1.5 text-sm">
        <span>{upload ? 'Replace retained file' : 'Choose a CSV file'}</span>
        <input
          name="file"
          type="file"
          accept=".csv,text/csv"
          required
          disabled={busy}
          className="min-w-0 text-xs file:mr-3 file:rounded-md file:border file:border-line file:px-3 file:py-1.5"
        />
      </label>
      <Button operation="v1_csv_upload" type="submit" disabled={busy}>
        {busy ? 'Working…' : upload ? 'Retain replacement' : 'Retain file'}
      </Button>
      <p className="w-full text-xs text-muted-foreground">
        UTF-8 CSV only · up to 10 MiB, 50,000 rows, 100 columns and 500,000
        cells. Retaining the file keeps its original bytes. Loading requires a
        separate reviewed candidate plan below.
      </p>
    </form>
  )
}
