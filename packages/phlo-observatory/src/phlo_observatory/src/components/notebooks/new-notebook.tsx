/** Dialog for a new notebook: a folder and a name, saved as an .ipynb file in the project. */
import * as React from 'react'
import { notebookFileName } from '@/lib/notebook'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function NewNotebookDialog({
  open,
  onOpenChange,
  folders,
  onCreate,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Existing folders, offered as suggestions */
  folders: ReadonlyArray<string>
  /** Resolves to an error message, or null once the notebook is open */
  onCreate: (path: string) => Promise<string | null>
}) {
  const [folder, setFolder] = React.useState('')
  const [name, setName] = React.useState('')
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  const listId = React.useId()
  const path = notebookFileName(
    [folder.trim().replace(/^\/+|\/+$/g, ''), name.trim()]
      .filter(Boolean)
      .join('/'),
  )

  const submit = async () => {
    if (!path || !name.trim()) {
      setError('Use letters, digits, "-" and "_". Folders are split by "/".')
      return
    }
    setBusy(true)
    const failure = await onCreate(path)
    setBusy(false)
    if (failure) return setError(failure)
    setName('')
    setError(null)
    onOpenChange(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[480px]">
        <DialogHeader>
          <DialogTitle>New notebook</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            void submit()
          }}
        >
          <DialogBody className="flex flex-col gap-3">
            <label className="flex flex-col gap-1 text-[13px]">
              Name
              <Input
                aria-label="Notebook name"
                placeholder="autoimmune-car-t"
                value={name}
                onChange={(event) => {
                  setName(event.target.value)
                  setError(null)
                }}
              />
            </label>
            <label className="flex flex-col gap-1 text-[13px]">
              Folder
              <Input
                aria-label="Folder"
                placeholder="market"
                list={listId}
                value={folder}
                onChange={(event) => {
                  setFolder(event.target.value)
                  setError(null)
                }}
                className="font-mono text-[12.5px]"
              />
              <datalist id={listId}>
                {folders.map((item) => (
                  <option key={item} value={item} />
                ))}
              </datalist>
            </label>
            <p className="text-xs text-muted-foreground">
              Saved as{' '}
              <code className="font-mono text-text-2">
                notebooks/{path ?? '…'}
              </code>
              , a plain Jupyter file you can commit, review and open in
              JupyterLab or VS Code.
            </p>
            {error ? (
              <p role="alert" className="text-xs text-bad-text">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="ghost"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={busy || !name.trim()}>
              {busy ? 'Creating…' : 'Create notebook'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
