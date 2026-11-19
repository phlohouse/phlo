/** Rename, move, duplicate and delete dialogs for notebook files. */
import * as React from 'react'
import { notebookFileName } from '@/lib/notebook'
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

export type FileAction =
  | { kind: 'rename'; path: string }
  | { kind: 'duplicate'; path: string }
  | { kind: 'delete'; path: string }

const copy = {
  rename: {
    title: 'Rename or move notebook',
    submit: 'Rename',
    busy: 'Renaming…',
    note: 'Saved outputs and run history move with it. Change the folder part to move it.',
  },
  duplicate: {
    title: 'Duplicate notebook',
    submit: 'Duplicate',
    busy: 'Duplicating…',
    note: 'Copies the saved cells and packages. The schedule is not copied.',
  },
}

function suggestion(action: FileAction) {
  const stem = action.path.replace(/\.ipynb$/, '')
  return action.kind === 'duplicate' ? `${stem}-copy` : stem
}

function PathForm({
  action,
  onSubmit,
  onClose,
}: {
  action: Extract<FileAction, { kind: 'rename' | 'duplicate' }>
  onSubmit: (to: string) => Promise<string | null>
  onClose: () => void
}) {
  const [value, setValue] = React.useState(() => suggestion(action))
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const text = copy[action.kind]
  const target = notebookFileName(value)
  const unchanged = target === action.path
  const submit = async () => {
    if (!target) {
      setError('Use letters, digits, "-" and "_". Folders are split by "/".')
      return
    }
    setBusy(true)
    const failure = await onSubmit(target)
    setBusy(false)
    if (failure) setError(failure)
    else onClose()
  }
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault()
        void submit()
      }}
    >
      <DialogBody className="flex flex-col gap-3">
        <label className="flex flex-col gap-1 text-[13px]">
          Path in notebooks/
          <Input
            aria-label="New path"
            value={value}
            onChange={(event) => {
              setValue(event.target.value)
              setError(null)
            }}
            className="font-mono text-[12.5px]"
          />
        </label>
        <p className="text-xs text-muted-foreground">
          {text.note} New path:{' '}
          <code className="font-mono text-text-2">
            notebooks/{target ?? '…'}
          </code>
        </p>
        {error ? (
          <p role="alert" className="text-xs text-bad-text">
            {error}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button type="submit" disabled={busy || !target || unchanged}>
          {busy ? text.busy : text.submit}
        </Button>
      </DialogFooter>
    </form>
  )
}

function DeleteForm({
  path,
  onSubmit,
  onClose,
}: {
  path: string
  onSubmit: () => Promise<string | null>
  onClose: () => void
}) {
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  return (
    <>
      <DialogBody className="flex flex-col gap-2 text-[13px] text-text-2">
        <p>
          <code className="font-mono">notebooks/{path}</code> and its saved
          outputs and run history are removed from the project. A version
          already committed stays in git history.
        </p>
        {error ? (
          <p role="alert" className="text-xs text-bad-text">
            {error}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <Button type="button" variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          variant="destructive"
          disabled={busy}
          onClick={async () => {
            setBusy(true)
            const failure = await onSubmit()
            setBusy(false)
            if (failure) setError(failure)
            else onClose()
          }}
        >
          {busy ? 'Deleting…' : 'Delete notebook'}
        </Button>
      </DialogFooter>
    </>
  )
}

export function NotebookFileDialog({
  action,
  onClose,
  onMove,
  onDuplicate,
  onDelete,
}: {
  action: FileAction | null
  onClose: () => void
  onMove: (from: string, to: string) => Promise<string | null>
  onDuplicate: (from: string, to: string) => Promise<string | null>
  onDelete: (path: string) => Promise<string | null>
}) {
  return (
    <Dialog open={action !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-[480px]">
        {action?.kind === 'delete' ? (
          <>
            <DialogHeader>
              <DialogTitle>Delete this notebook?</DialogTitle>
              <DialogDescription>This cannot be undone here.</DialogDescription>
            </DialogHeader>
            <DeleteForm
              path={action.path}
              onClose={onClose}
              onSubmit={() => onDelete(action.path)}
            />
          </>
        ) : action ? (
          <>
            <DialogHeader>
              <DialogTitle>{copy[action.kind].title}</DialogTitle>
            </DialogHeader>
            <PathForm
              key={`${action.kind}:${action.path}`}
              action={action}
              onClose={onClose}
              onSubmit={(to) =>
                action.kind === 'rename'
                  ? onMove(action.path, to)
                  : onDuplicate(action.path, to)
              }
            />
          </>
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
