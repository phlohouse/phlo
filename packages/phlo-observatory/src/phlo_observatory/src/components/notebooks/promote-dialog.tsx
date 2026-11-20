/** Turn a Trino SQL cell into a dbt model in the project, previewing the files first. */
import * as React from 'react'
import { Loader2Icon } from 'lucide-react'
import type { PromotePlan } from '@/lib/data/api/notebook-publish'
import type { NotebookCell } from '@/lib/notebook'
import { promoteNotebookCell } from '@/lib/data/api/notebook-publish'
import { modelName } from '@/lib/notebook'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

const MODEL = /^[a-z_][a-z0-9_]{0,63}$/

function PlanView({ plan }: { plan: PromotePlan | null }) {
  return (
    <>
      {plan?.files.map((file) => (
        <figure key={file.path} className="m-0 flex flex-col gap-1">
          <figcaption className="font-mono text-[12px] text-text-2">
            {file.path}
            {file.exists ? ' (exists)' : ''}
          </figcaption>
          <pre className="m-0 max-h-[200px] overflow-auto rounded-lg border border-line bg-raised px-3 py-2 font-mono text-[12px] leading-5 whitespace-pre text-text-2">
            {file.content}
          </pre>
        </figure>
      ))}
      {plan?.applied ? (
        <p role="status" className="text-[13px] text-ok-text">
          Written. dbt picks the model up on the next build; commit it from
          Share, Commit to git.
        </p>
      ) : (
        <p className="text-xs text-muted-foreground">
          Tables become ref() when the project has a dbt model of that name,
          source() when a source declares them, and stay as schema.table
          otherwise. Nothing is overwritten.
        </p>
      )}
    </>
  )
}

function PromoteForm({
  env,
  path,
  cell,
  unsaved,
  onClose,
}: {
  env: string
  path: string
  cell: NotebookCell
  unsaved: boolean
  onClose: () => void
}) {
  const [model, setModel] = React.useState(() => modelName(cell))
  const [plan, setPlan] = React.useState<PromotePlan | null>(null)
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState<'preview' | 'write' | null>(null)
  const valid = MODEL.test(model)
  const call = async (apply: boolean) => {
    setBusy(apply ? 'write' : 'preview')
    setError(null)
    const result = await promoteNotebookCell({
      data: { env, path, cell_id: cell.id, model, apply },
    }).catch((failure: unknown) => ({
      state: 'refused' as const,
      message: failure instanceof Error ? failure.message : 'Promote failed.',
    }))
    setBusy(null)
    if (result.state === 'ok') setPlan(result.data)
    else setError(result.message)
  }
  React.useEffect(() => {
    if (valid) void call(false)
  }, [])
  const exists = plan?.files.some((file) => file.exists) ?? false
  return (
    <>
      <DialogBody className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-[13px]">
          Model name
          <div className="flex gap-2">
            <Input
              aria-label="Model name"
              value={model}
              onChange={(event) => {
                setModel(event.target.value)
                setPlan(null)
              }}
              aria-invalid={!valid}
              className="font-mono text-[12.5px]"
            />
            <Button
              variant="outline"
              disabled={!valid || busy !== null}
              onClick={() => void call(false)}
            >
              Preview
            </Button>
          </div>
        </label>
        {unsaved ? (
          <p className="text-[12.5px] text-warn-ink">
            The notebook has unsaved changes. The model is made from the saved
            version of this cell.
          </p>
        ) : null}
        {busy === 'preview' ? (
          <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
            <Loader2Icon className="size-3.5 animate-spin" /> Rewriting table
            references
          </p>
        ) : null}
        <PlanView plan={plan} />
        {error ? (
          <p role="alert" className="text-xs text-bad-text">
            {error}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          {plan?.applied ? 'Done' : 'Cancel'}
        </Button>
        {plan?.applied ? null : (
          <Button
            disabled={!plan || exists || busy !== null}
            onClick={() => void call(true)}
          >
            {busy === 'write' ? 'Writing…' : 'Write model files'}
          </Button>
        )}
      </DialogFooter>
    </>
  )
}

export function PromoteDialog({
  env,
  path,
  cell,
  unsaved,
  onClose,
}: {
  env: string
  path: string
  cell: NotebookCell | null
  unsaved: boolean
  onClose: () => void
}) {
  return (
    <Dialog open={cell !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[640px]">
        <DialogHeader>
          <DialogTitle>Promote to a dbt model</DialogTitle>
          <DialogDescription>
            Makes this query part of the pipeline, under
            workflows/transforms/dbt/models/notebooks.
          </DialogDescription>
        </DialogHeader>
        {cell ? (
          <PromoteForm
            key={cell.id}
            env={env}
            path={path}
            cell={cell}
            unsaved={unsaved}
            onClose={onClose}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
