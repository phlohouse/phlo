/** Reviews source-only authoring before writing project workflow files. */
import * as React from 'react'
import type { SourceWorkflowInput } from '@/lib/data/api/sources'
import {
  createSourceWorkflow,
  sourceWorkflowInputSchema,
} from '@/lib/data/api/sources'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'

export function RestApiForm({ onClose }: { onClose: () => void }) {
  const [review, setReview] = React.useState<SourceWorkflowInput | null>(null)
  const [error, setError] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [result, setResult] = React.useState<Awaited<
    ReturnType<typeof createSourceWorkflow>
  > | null>(null)
  async function create() {
    if (!review) return
    setBusy(true)
    setError('')
    try {
      setResult(await createSourceWorkflow({ data: review }))
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Workflow creation failed. Inspect existing project files before retrying.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <section
      className="rounded-xl border border-line bg-card p-5"
      aria-labelledby="source-form-title"
    >
      <div className="mb-3 flex items-center justify-between gap-4">
        <h2 id="source-form-title" className="text-[15px] font-medium">
          REST API source
        </h2>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      <p className="mb-4 text-sm text-muted-foreground">
        Create DLT workflow and schema files in this deployment's project. This
        requires project:write permission. Loading data, scheduling and branch
        review are separate steps.
      </p>
      {result ? (
        <div role="status" className="space-y-3 text-sm">
          <h3 className="font-medium">Workflow files created</h3>
          <ul className="list-disc space-y-1 pl-5">
            {result.files.map((file) => (
              <li key={file} className="break-all font-mono text-xs">
                {file}
              </li>
            ))}
          </ul>
          {result.validations.map((item) => (
            <p key={item.file}>
              {item.valid ? 'Validation passed' : 'Validation failed'}:{' '}
              <code>{item.file}</code>
              {item.errors.length ? ` · ${item.errors.join('; ')}` : ''}
            </p>
          ))}
          <p className="text-muted-foreground">
            These files are not active yet. Review the generated source,
            configure credentials in the secret store, and reload the repository
            before materializing.
          </p>
          <ol className="list-decimal space-y-1 pl-5">
            {result.next_steps.map((step) => (
              <li key={step}>{step}</li>
            ))}
          </ol>
        </div>
      ) : review ? (
        <div className="space-y-4 text-sm">
          <h3 className="font-medium">Review project changes</h3>
          <dl className="grid grid-cols-[100px_1fr] gap-2">
            <dt>Provider</dt>
            <dd>DLT REST API</dd>
            <dt>Domain</dt>
            <dd>{review.domain}</dd>
            <dt>Table</dt>
            <dd>{review.table}</dd>
            <dt>Unique key</dt>
            <dd>{review.unique_key}</dd>
            <dt>API base URL</dt>
            <dd className="break-all">{review.api_base_url}</dd>
            <dt>Columns</dt>
            <dd>{review.fields.join(', ')}</dd>
          </dl>
          <p className="text-muted-foreground">
            The API writes workflow, schema and test files. Existing targets are
            rejected. No data is loaded by this action.
          </p>
          <div className="flex gap-2">
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setReview(null)}
            >
              Edit
            </Button>
            <Button disabled={busy} onClick={() => void create()}>
              {busy ? 'Creating files…' : 'Create workflow files'}
            </Button>
          </div>
        </div>
      ) : (
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(event) => {
            event.preventDefault()
            const values = new FormData(event.currentTarget)
            const parsed = sourceWorkflowInputSchema.safeParse({
              domain: values.get('domain'),
              table: values.get('table'),
              unique_key: values.get('unique_key'),
              api_base_url: values.get('api_base_url'),
              fields: String(values.get('fields'))
                .split(',')
                .map((field) => field.trim())
                .filter(Boolean),
            })
            if (parsed.success) {
              setError('')
              setReview(parsed.data)
            } else
              setError(
                parsed.error.issues
                  .map((issue) => `${issue.path.join('.')}: ${issue.message}`)
                  .join(' '),
              )
          }}
        >
          {(
            [
              { name: 'domain', label: 'Domain', placeholder: 'lab' },
              { name: 'table', label: 'Table', placeholder: 'samples' },
              {
                name: 'unique_key',
                label: 'Unique key',
                placeholder: 'sample_id',
              },
              {
                name: 'api_base_url',
                label: 'API base URL',
                placeholder: 'https://example.org/api',
              },
              {
                name: 'fields',
                label: 'Columns as name:type, separated by commas',
                placeholder: 'sample_id:str, result:float?',
              },
            ] as const
          ).map((field) => (
            <label key={field.name} className="flex flex-col gap-1.5 text-sm">
              <span>{field.label}</span>
              <Input
                name={field.name}
                placeholder={field.placeholder}
                required
              />
            </label>
          ))}
          <div className="flex items-end">
            <Button type="submit">Review workflow files</Button>
          </div>
        </form>
      )}
      {error ? (
        <p role="alert" className="mt-3 text-sm text-bad-text">
          {error}
        </p>
      ) : null}
    </section>
  )
}
