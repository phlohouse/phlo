/** Source editing is revisioned separately from Git and Nessie data branches. */
import * as React from 'react'
import { useBlocker } from '@tanstack/react-router'
import { MaximizeIcon, MinimizeIcon } from 'lucide-react'
import { CandidateExecutionPanel } from './execution-panel'
import { DraftComparison, RevisionHistory } from './revision-history'
import type { TransformModel } from '@/lib/data/api/transform'
import type {
  TransformCompilation,
  TransformDraft,
  TransformDraftSummary,
  TransformSource,
} from '@/lib/data/api/transform-editor'
import {
  compileTransformDraft,
  getTransformDraft,
  getTransformSource,
  saveTransformDraft,
} from '@/lib/data/api/transform-editor'
import { SqlEditor } from '@/components/query/sql-editor'
import { PageHeader } from '@/components/phlo/page'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Segmented } from '@/components/ui/toggle-group'
import { cn } from '@/lib/utils'
import { useActionPermission } from '@/components/phlo/action-availability'

type Props = {
  env: string
  model: TransformModel
  focus: boolean
  draftId?: string
  revision?: number
  onFocus: () => void
  onClose: () => void
  onSaved: (draft: TransformDraft) => void
}
function useTransformEditor({ env, model, draftId, revision, onSaved }: Props) {
  const compilePermission = useActionPermission('v1_transform_model_compile')
  const mounted = React.useRef(true)
  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  const [source, setSource] = React.useState<TransformSource>()
  const [draft, setDraft] = React.useState<TransformDraft>()
  const [sql, setSql] = React.useState('')
  const latestSql = React.useRef('')
  const [compilation, setCompilation] = React.useState<TransformCompilation>()
  const [error, setError] = React.useState<string>()
  const [busy, setBusy] = React.useState<'save' | 'compile' | 'history' | null>(
    null,
  )
  const [view, setView] = React.useState('source')
  const selectedIdentity = React.useRef('')
  const navigationVersion = React.useRef(0)
  React.useEffect(() => {
    const identity = JSON.stringify([env, model.model, draftId, revision])
    if (selectedIdentity.current === identity) return
    navigationVersion.current += 1
    selectedIdentity.current = ''
    let active = true
    setBusy('history')
    setSource(undefined)
    setDraft(undefined)
    setSql('')
    latestSql.current = ''
    setCompilation(undefined)
    Promise.all([
      getTransformSource({ data: { env, model: model.model } }),
      draftId && revision
        ? getTransformDraft({
            data: { env, model: model.model, draft_id: draftId, revision },
          })
        : Promise.resolve(undefined),
    ]).then(
      ([next, restored]) => {
        if (!active) return
        selectedIdentity.current = identity
        setSource(next)
        setDraft(restored)
        setSql(restored?.sql ?? next.sql)
        latestSql.current = restored?.sql ?? next.sql
        setCompilation(undefined)
        setError(undefined)
        setView('source')
        setBusy(null)
      },
      (caught) => {
        if (active) {
          setBusy(null)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Could not read the model source.',
          )
        }
      },
    )
    return () => {
      active = false
    }
  }, [env, model.model, draftId, revision])
  const dirty = sql !== (draft?.sql ?? source?.sql ?? '')
  useBlocker({
    shouldBlockFn: ({ next }) => {
      if (!dirty) return false
      if (next.routeId === '/_app/transform') {
        const sameRevision =
          next.search.env === env &&
          next.search.model === model.model &&
          next.search.draft === draftId &&
          next.search.revision === revision
        // Saving updates the URL after the saved identity is already applied locally.
        const savedIdentity =
          JSON.stringify([
            next.search.env,
            next.search.model,
            next.search.draft,
            next.search.revision,
          ]) === selectedIdentity.current
        if (sameRevision || savedIdentity) return false
      }
      return !window.confirm(
        'Discard unsaved SQL changes and leave this revision?',
      )
    },
    enableBeforeUnload: dirty,
  })
  const save = async () => {
    if (!source || busy) return
    const requestVersion = navigationVersion.current
    setBusy('save')
    setError(undefined)
    try {
      const saved = await saveTransformDraft({
        data: {
          env,
          model: model.model,
          sql: latestSql.current,
          source_digest: source.source_digest,
          workspace_digest: source.workspace_digest,
          draft_id: draft?.draft_id,
          expected_revision: draft?.revision ?? 0,
        },
      })
      if (!mounted.current || requestVersion !== navigationVersion.current)
        return
      selectedIdentity.current = JSON.stringify([
        env,
        model.model,
        saved.draft_id,
        saved.revision,
      ])
      setDraft(saved)
      onSaved(saved)
    } catch (caught) {
      if (!mounted.current || requestVersion !== navigationVersion.current)
        return
      setError(
        caught instanceof Error
          ? caught.message
          : 'Could not save the draft revision.',
      )
    } finally {
      if (mounted.current && requestVersion === navigationVersion.current)
        setBusy(null)
    }
  }
  const compile = async () => {
    if (!draft || dirty || busy || !compilePermission.allowed) return
    const requestVersion = navigationVersion.current
    setBusy('compile')
    setError(undefined)
    try {
      const result = await compileTransformDraft({
        data: {
          env,
          model: model.model,
          draft_id: draft.draft_id,
          revision: draft.revision,
        },
      })
      if (!mounted.current || requestVersion !== navigationVersion.current)
        return
      setCompilation(result)
      setView('compiled')
    } catch (caught) {
      if (!mounted.current || requestVersion !== navigationVersion.current)
        return
      setError(
        caught instanceof Error
          ? caught.message
          : 'Could not compile this revision.',
      )
    } finally {
      if (mounted.current && requestVersion === navigationVersion.current)
        setBusy(null)
    }
  }
  const openRevision = async (selected: TransformDraftSummary) => {
    if (dirty || busy) return
    const requestVersion = navigationVersion.current
    const previousSql = latestSql.current
    setBusy('history')
    setError(undefined)
    try {
      const restored = await getTransformDraft({
        data: {
          env,
          model: model.model,
          draft_id: selected.draft_id,
          revision: selected.revision,
        },
      })
      if (!mounted.current || requestVersion !== navigationVersion.current)
        return
      if (latestSql.current !== previousSql)
        throw new Error(
          'The editor changed while opening the revision. Save your changes before switching.',
        )
      setDraft(restored)
      setSql(restored.sql)
      latestSql.current = restored.sql
      selectedIdentity.current = JSON.stringify([
        env,
        model.model,
        restored.draft_id,
        restored.revision,
      ])
      setCompilation(undefined)
      setView('source')
      onSaved(restored)
    } catch (caught) {
      if (mounted.current && requestVersion === navigationVersion.current)
        setError(
          caught instanceof Error
            ? caught.message
            : 'Could not open the saved revision.',
        )
    } finally {
      if (mounted.current && requestVersion === navigationVersion.current)
        setBusy(null)
    }
  }
  const changeSql = (value: string) => {
    latestSql.current = value
    setSql(value)
  }
  return {
    source,
    draft,
    sql,
    compilation,
    error,
    busy,
    view,
    setView,
    dirty,
    save,
    compile,
    changeSql,
    openRevision,
    env,
  }
}

type EditorState = ReturnType<typeof useTransformEditor>

export function TransformEditor({
  env,
  model,
  focus,
  draftId,
  revision,
  onFocus,
  onClose,
  onSaved,
}: Props) {
  const state = useTransformEditor({
    env,
    model,
    focus,
    draftId,
    revision,
    onFocus,
    onClose,
    onSaved,
  })
  const { source, draft, compilation, error, view, setView, dirty } = state
  return (
    <section
      aria-label="Transform source editor"
      className={cn(
        'flex min-h-0 flex-1 flex-col overflow-hidden bg-card',
        focus && 'fixed inset-2 z-40 rounded-xl border border-line',
      )}
    >
      <PageHeader
        title={model.model}
        crumbs={[
          { label: 'Pipelines', to: '/pipelines' },
          { label: 'Transformations', to: '/transform' },
        ]}
        meta={
          draft
            ? `Draft revision ${draft.revision} · ${env}`
            : `Workspace source · ${env}`
        }
        actions={
          <EditorActions
            state={state}
            onFocus={onFocus}
            onClose={onClose}
            focus={focus}
          />
        }
      />
      <div className="flex flex-wrap items-center gap-3 border-b border-line bg-branch-soft px-5 py-3 text-xs text-muted-foreground">
        <Badge variant="branch">SQL draft</Badge>
        <span>
          Revisions are stored separately from workspace source. Candidate runs
          use a separate data branch and do not publish to the base reference.
        </span>
        {dirty ? <span className="text-warn-text">Unsaved changes</span> : null}
      </div>
      <RevisionHistory
        env={env}
        model={model.model}
        draft={draft}
        disabled={Boolean(state.busy)}
        dirty={dirty}
        onOpen={(selected) => void state.openRevision(selected)}
      />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        <aside
          aria-label="Model files"
          className="hidden w-[235px] shrink-0 border-r border-line p-4 lg:block"
        >
          <p className="mt-0 font-mono text-xs">phlo-transform</p>
          <p className="break-all rounded-lg bg-primary-soft p-3 font-mono text-xs">
            {source?.path ?? model.path ?? model.model}
          </p>
          <p className="text-xs text-muted-foreground">
            {model.materialization ?? 'Model'} · {model.dependencies.length}{' '}
            upstream assets
          </p>
          {draft ? (
            <p className="break-all font-mono text-[11px] text-muted-foreground">
              revision {draft.revision}
              <br />
              {draft.revision_digest.slice(0, 16)}
            </p>
          ) : null}
        </aside>
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-auto">
          <div className="flex shrink-0 flex-wrap items-center gap-3 border-b border-line px-4 py-2">
            <span className="min-w-0 flex-1 truncate font-mono text-xs">
              {source?.path ?? 'Loading source…'}
            </span>
            <Segmented
              aria-label="Model source view"
              value={view}
              onValueChange={setView}
              options={[
                { value: 'source', label: 'Source' },
                { value: 'compiled', label: 'Compiled SQL' },
                { value: 'compare', label: 'Compare source' },
              ]}
            />
          </div>
          <EditorContent state={state} model={model.model} />
          <CompilationEvidence
            compilation={compilation}
            dirty={compiledRevisionDiffers(compilation, draft, dirty)}
          />
          {draft ? (
            <CandidateExecutionPanel
              key={`${draft.draft_id}:${draft.revision}`}
              env={env}
              model={model.model}
              draft={draft}
              compilation={compilation}
              dirty={dirty}
            />
          ) : null}
          {error ? (
            <p role="alert" className="mx-5 text-sm text-bad-text">
              {error}
            </p>
          ) : null}
        </div>
      </div>
    </section>
  )
}

function EditorActions({
  state,
  onFocus,
  onClose,
  focus,
}: {
  state: EditorState
  onFocus: () => void
  onClose: () => void
  focus: boolean
}) {
  const { source, draft, dirty, busy, save, compile } = state
  return (
    <>
      <div className="flex items-center gap-2">
        <Button variant="outline" onClick={onFocus}>
          {focus ? <MinimizeIcon /> : <MaximizeIcon />}
          {focus ? 'Exit focus' : 'Focus'}
        </Button>
        <Button variant="outline" onClick={onClose}>
          Workspace
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Button
          operation="v1_transform_model_compile"
          variant="outline"
          disabled={!draft || dirty || Boolean(busy)}
          onClick={() => void compile()}
        >
          {busy === 'compile' ? 'Compiling…' : 'Compile and plan'}
        </Button>
        <Button
          operation="v1_transform_model_draft_save"
          disabled={!source || Boolean(busy) || (!dirty && Boolean(draft))}
          onClick={() => void save()}
        >
          {busy === 'save' ? 'Saving…' : 'Save draft revision'}
        </Button>
      </div>
    </>
  )
}

function EditorContent({
  state,
  model,
}: {
  state: EditorState
  model: string
}) {
  const { source, view, sql, changeSql, compile, compilation, error } = state
  if (view === 'compare')
    return state.draft ? (
      <DraftComparison
        key={`${state.draft.draft_id}:${state.draft.revision}`}
        env={state.env}
        model={model}
        draft={state.draft}
      />
    ) : (
      <p className="p-5 text-sm text-muted-foreground">
        Save a SQL revision before comparing it with workspace source.
      </p>
    )
  return (
    <>
      {source ? (
        view === 'source' ? (
          <SqlEditor
            value={sql}
            onChange={changeSql}
            label={`SQL source for ${model}`}
            onRun={() => void compile()}
            className="min-h-[320px] flex-1"
          />
        ) : (
          <pre
            role="region"
            aria-label="Compiled model SQL"
            tabIndex={0}
            className="m-0 min-h-[320px] flex-1 overflow-auto p-5 font-mono text-xs leading-6 whitespace-pre-wrap"
          >
            {compilation?.compiled_sql ??
              'Save a draft revision, then compile it to see the engine output.'}
          </pre>
        )
      ) : !error ? (
        <p role="status" className="p-5 text-sm text-muted-foreground">
          Reading actual model source…
        </p>
      ) : null}
    </>
  )
}

function CompilationEvidence({
  compilation,
  dirty,
}: {
  compilation?: TransformCompilation
  dirty: boolean
}) {
  if (!compilation)
    return (
      <p className="m-0 border-t border-line p-4 text-xs text-muted-foreground">
        Compile and plan uses the native engine. This step does not build a
        table.
      </p>
    )
  return (
    <div
      aria-label="Compilation evidence"
      className="grid shrink-0 gap-3 border-t border-line p-4 text-xs"
    >
      <div className="flex flex-wrap items-center gap-3">
        <Badge variant={compilation.status === 'ready' ? 'ok' : 'bad'}>
          {compilation.status === 'ready'
            ? 'Read-only plan ready'
            : 'Compilation failed'}
        </Badge>
        <span>
          Revision {compilation.revision} · ref {compilation.ref}
        </span>
        {dirty ? (
          <span className="text-warn-text">
            The editor differs from this compiled revision.
          </span>
        ) : null}
      </div>
      {compilation.target ? (
        <p className="m-0 break-all font-mono">Target: {compilation.target}</p>
      ) : null}
      {compilation.plan_id ? (
        <p className="m-0 break-all font-mono text-muted-foreground">
          Plan {compilation.plan_id} · SQL {compilation.sql_hash}
        </p>
      ) : null}
      {compilation.diagnostics.length ? (
        <pre className="m-0 max-h-48 overflow-auto rounded-lg bg-background p-3 font-mono text-xs whitespace-pre-wrap">
          {JSON.stringify(compilation.diagnostics, null, 2)}
        </pre>
      ) : (
        <p className="m-0 text-muted-foreground">
          The engine reported no compilation diagnostics.
        </p>
      )}
    </div>
  )
}

function compiledRevisionDiffers(
  compilation: TransformCompilation | undefined,
  draft: TransformDraft | undefined,
  dirty: boolean,
) {
  return (
    dirty || Boolean(compilation && draft?.revision !== compilation.revision)
  )
}
