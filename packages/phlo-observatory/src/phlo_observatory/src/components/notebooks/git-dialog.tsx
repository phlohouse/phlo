/** Commit saved notebooks, and models promoted from them, to the project's git branch. */
import * as React from 'react'
import { GitBranchIcon, Loader2Icon } from 'lucide-react'
import type { GitState } from '@/lib/data/api/notebook-publish'
import {
  commitNotebookFiles,
  getNotebookGit,
} from '@/lib/data/api/notebook-publish'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Textarea } from '@/components/ui/input'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Mono } from '@/components/phlo/status'

function Note({
  children,
  alert,
}: {
  children: React.ReactNode
  alert?: boolean
}) {
  return (
    <DialogBody>
      <p
        role={alert ? 'alert' : undefined}
        className={
          alert ? 'text-[13px] text-bad-text' : 'text-[13px] text-text-2'
        }
      >
        {children}
      </p>
    </DialogBody>
  )
}

function GitForm({
  env,
  path,
  dirty,
  onClose,
}: {
  env: string
  path: string
  dirty: boolean
  onClose: () => void
}) {
  const [git, setGit] = React.useState<GitState | null>(null)
  const [failed, setFailed] = React.useState<string | null>(null)
  React.useEffect(() => {
    getNotebookGit({ data: { env } }).then(setGit, (error: unknown) =>
      setFailed(error instanceof Error ? error.message : 'Git is unavailable.'),
    )
  }, [env])
  if (failed) return <Note alert>{failed}</Note>
  if (!git)
    return (
      <DialogBody>
        <p className="flex items-center gap-2 text-[13px] text-muted-foreground">
          <Loader2Icon className="size-3.5 animate-spin" /> Reading git status
        </p>
      </DialogBody>
    )
  if (!git.enabled)
    return <Note>{git.message ?? 'Commits from Observatory are off.'}</Note>
  return (
    <CommitForm
      env={env}
      path={path}
      dirty={dirty}
      git={git}
      onClose={onClose}
    />
  )
}

function CommitForm({
  env,
  path,
  dirty,
  git,
  onClose,
}: {
  env: string
  path: string
  dirty: boolean
  git: GitState
  onClose: () => void
}) {
  const [chosen, setChosen] = React.useState<Set<string>>(
    () =>
      new Set(
        git.changed.filter(
          (item) =>
            item === `notebooks/${path}` ||
            item.startsWith('workflows/transforms/dbt/models/notebooks/'),
        ),
      ),
  )
  const [message, setMessage] = React.useState(
    `Update notebook ${path.replace(/\.ipynb$/, '')}`,
  )
  const [busy, setBusy] = React.useState(false)
  const [done, setDone] = React.useState<string | null>(null)
  const [refused, setRefused] = React.useState<string | null>(null)
  if (done)
    return (
      <>
        <Note>
          Committed <Mono>{done.slice(0, 12)}</Mono> to{' '}
          <Mono>{git.branch}</Mono>. Push and open a pull request from your
          usual git tools.
        </Note>
        <DialogFooter>
          <Button onClick={onClose}>Done</Button>
        </DialogFooter>
      </>
    )
  const toggle = (item: string) =>
    setChosen((current) => {
      const next = new Set(current)
      if (next.has(item)) next.delete(item)
      else next.add(item)
      return next
    })
  const commit = async () => {
    setBusy(true)
    setRefused(null)
    const result = await commitNotebookFiles({
      data: { env, paths: [...chosen], message: message.trim() },
    }).catch((error: unknown) => ({
      state: 'refused' as const,
      message: error instanceof Error ? error.message : 'Commit failed.',
    }))
    setBusy(false)
    if (result.state === 'ok') setDone(result.data.commit)
    else setRefused(result.message)
  }
  return (
    <>
      <DialogBody className="flex flex-col gap-3">
        <p className="flex items-center gap-1.5 text-[13px] text-text-2">
          <GitBranchIcon className="size-3.5 text-branch" />
          Branch <Mono>{git.branch ?? 'detached HEAD'}</Mono>
        </p>
        {git.protected ? (
          <p className="rounded-lg border border-warn-soft bg-warn-wash px-3 py-2 text-[12.5px] text-warn-ink">
            Observatory does not commit to {git.branch ?? 'a detached HEAD'}.
            Switch the project checkout to a working branch first.
          </p>
        ) : null}
        {dirty ? (
          <p className="text-[12.5px] text-warn-ink">
            This notebook has unsaved changes. Only the saved file is committed.
          </p>
        ) : null}
        <ChangedFiles changed={git.changed} chosen={chosen} toggle={toggle} />
        <label className="flex flex-col gap-1 text-[13px]">
          Message
          <Textarea
            aria-label="Commit message"
            rows={3}
            value={message}
            onChange={(event) => setMessage(event.target.value)}
          />
        </label>
        <p className="text-xs text-muted-foreground">
          Only the files you tick go into the commit, with you as its author.
          Anything else already staged is left alone.
        </p>
        {refused ? (
          <p role="alert" className="text-xs text-bad-text">
            {refused}
          </p>
        ) : null}
      </DialogBody>
      <DialogFooter>
        <Button variant="ghost" onClick={onClose}>
          Cancel
        </Button>
        <Button
          disabled={
            busy || git.protected || chosen.size === 0 || !message.trim()
          }
          onClick={() => void commit()}
        >
          {busy ? 'Committing…' : `Commit ${chosen.size || ''}`.trim()}
        </Button>
      </DialogFooter>
    </>
  )
}

function ChangedFiles({
  changed,
  chosen,
  toggle,
}: {
  changed: ReadonlyArray<string>
  chosen: ReadonlySet<string>
  toggle: (item: string) => void
}) {
  if (changed.length === 0)
    return (
      <p className="text-[13px] text-muted-foreground">
        No notebook or promoted model has changes to commit.
      </p>
    )
  return (
    <ul className="flex max-h-[180px] flex-col gap-1.5 overflow-auto">
      {changed.map((item) => (
        <li key={item}>
          <label className="flex items-center gap-2 text-[12.5px]">
            <Checkbox
              checked={chosen.has(item)}
              onCheckedChange={() => toggle(item)}
              aria-label={`Commit ${item}`}
            />
            <span className="min-w-0 truncate font-mono">{item}</span>
          </label>
        </li>
      ))}
    </ul>
  )
}

export function GitDialog({
  open,
  onOpenChange,
  env,
  path,
  dirty,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  env: string
  path: string
  dirty: boolean
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Commit to git</DialogTitle>
          <DialogDescription>
            Notebooks are plain .ipynb files without outputs, so a commit holds
            only code and text.
          </DialogDescription>
        </DialogHeader>
        {open ? (
          <GitForm
            env={env}
            path={path}
            dirty={dirty}
            onClose={() => onOpenChange(false)}
          />
        ) : null}
      </DialogContent>
    </Dialog>
  )
}
