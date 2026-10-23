/** Actual saved revisions and source comparisons preserve source/data branch distinctions. */
import * as React from 'react'
import type {
  TransformDraft,
  TransformDraftComparison,
  TransformDraftSummary,
} from '@/lib/data/api/transform-editor'
import {
  compareTransformDraft,
  getTransformDrafts,
} from '@/lib/data/api/transform-editor'

type HistoryProps = {
  env: string
  model: string
  draft?: TransformDraft
  disabled: boolean
  dirty: boolean
  onOpen: (revision: TransformDraftSummary) => void
}
export function RevisionHistory({
  env,
  model,
  draft,
  disabled,
  dirty,
  onOpen,
}: HistoryProps) {
  const [items, setItems] = React.useState<Array<TransformDraftSummary>>([])
  const [total, setTotal] = React.useState<number>()
  const [truncated, setTruncated] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const [archived, setArchived] = React.useState(false)
  React.useEffect(() => {
    let active = true
    getTransformDrafts({
      data: { env, model, include_archived: archived },
    }).then(
      (page) => {
        if (!active) return
        setItems(page.items)
        setTotal(page.total)
        setTruncated(page.truncated)
        setError(undefined)
      },
      (caught) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Revision history is unavailable.',
          )
      },
    )
    return () => {
      active = false
    }
  }, [
    env,
    model,
    draft?.draft_id,
    draft?.revision,
    draft?.archived_at,
    archived,
  ])
  const value = draft ? `${draft.draft_id}:${draft.revision}` : ''
  const choose = (selected: string) => {
    const entry = items.find(
      (item) => `${item.draft_id}:${item.revision}` === selected,
    )
    if (entry) onOpen(entry)
  }
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-3 border-b border-line px-5 py-2 text-xs">
      <label
        htmlFor="transform-revision-history"
        className="shrink-0 text-muted-foreground"
      >
        Saved revisions
      </label>
      <select
        id="transform-revision-history"
        aria-label="Saved SQL revision"
        value={value}
        disabled={disabled || dirty || items.length === 0}
        onChange={(event) => choose(event.target.value)}
        className="min-w-0 max-w-full basis-full rounded-md border border-line bg-background px-2 py-1.5 text-foreground sm:basis-80"
      >
        {!draft ? <option value="">Workspace source</option> : null}
        {draft &&
        !items.some((item) => `${item.draft_id}:${item.revision}` === value) ? (
          <option value={value}>Revision {draft.revision}</option>
        ) : null}
        {items.map((item) => (
          <option
            key={`${item.draft_id}:${item.revision}`}
            value={`${item.draft_id}:${item.revision}`}
          >
            Revision {item.revision} · {item.draft_id.slice(0, 8)} ·{' '}
            {new Date(item.created_at).toLocaleString()}
            {item.archived_at ? ' · Archived' : ''}
          </option>
        ))}
      </select>
      <label className="flex items-center gap-2 text-muted-foreground">
        <input
          type="checkbox"
          checked={archived}
          onChange={(event) => setArchived(event.target.checked)}
        />
        Include archived revisions
      </label>
      <span className="text-muted-foreground">
        {total === undefined
          ? 'Reading saved revisions…'
          : `${truncated ? `${items.length} of ${total}` : total} saved ${total === 1 ? 'revision' : 'revisions'}`}
      </span>
      {dirty ? (
        <span className="text-warn-text">
          Save changes before switching revisions.
        </span>
      ) : null}
      {error ? (
        <span role="alert" className="text-bad-text">
          {error}
        </span>
      ) : null}
    </div>
  )
}
export function DraftComparison({
  env,
  model,
  draft,
}: {
  env: string
  model: string
  draft: TransformDraft
}) {
  const [comparison, setComparison] = React.useState<TransformDraftComparison>()
  const [error, setError] = React.useState<string>()
  React.useEffect(() => {
    let active = true
    compareTransformDraft({
      data: { env, model, draft_id: draft.draft_id, revision: draft.revision },
    }).then(
      (value) => {
        if (active) setComparison(value)
      },
      (caught) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Source comparison is unavailable.',
          )
      },
    )
    return () => {
      active = false
    }
  }, [env, model, draft.draft_id, draft.revision])
  if (error)
    return (
      <p role="alert" className="p-5 text-sm text-bad-text">
        {error}
      </p>
    )
  if (!comparison)
    return (
      <p role="status" className="p-5 text-sm text-muted-foreground">
        Comparing saved SQL with actual workspace source…
      </p>
    )
  return (
    <div className="grid min-h-80 min-w-0 content-start gap-3 p-4 text-xs">
      <div className="flex flex-wrap gap-3">
        <span>
          Current workspace source → saved revision {comparison.revision}
        </span>
        <span className="text-ok-text">+{comparison.added_lines} lines</span>
        <span className="text-bad-text">−{comparison.removed_lines} lines</span>
      </div>
      <p className="m-0 text-muted-foreground">
        This compares SQL source files. Candidate data branch changes are shown
        with their native run results.
      </p>
      {!comparison.baseline_matches_saved_source ? (
        <p className="m-0 text-warn-text">
          Workspace source changed after this revision was saved. The comparison
          uses the current source.
        </p>
      ) : null}
      <pre
        role="region"
        aria-label="SQL source comparison"
        tabIndex={0}
        className="m-0 max-w-full overflow-auto rounded-lg border border-line p-3 font-mono leading-6"
      >
        {comparison.unified_diff.length
          ? comparison.unified_diff.map((line, index) => (
              <span
                key={index}
                className={
                  line.startsWith('+')
                    ? 'block bg-ok/10 text-ok-text'
                    : line.startsWith('-')
                      ? 'block bg-bad/10 text-bad-text'
                      : 'block text-muted-foreground'
                }
              >
                {line || ' '}
              </span>
            ))
          : 'Saved SQL matches the current workspace source.'}
      </pre>
    </div>
  )
}
