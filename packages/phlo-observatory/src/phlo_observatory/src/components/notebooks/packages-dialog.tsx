/** The notebook's own Python packages, and what the running kernel can import. */
import * as React from 'react'
import { Loader2Icon, SearchIcon } from 'lucide-react'
import type { KernelPackages } from '@/lib/data/api/notebooks'
import { getKernelPackages } from '@/lib/data/api/notebooks'
import { parsePackages } from '@/lib/notebook'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input, Textarea } from '@/components/ui/input'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

function Installed({ env, kernel }: { env: string; kernel: string | null }) {
  const [state, setState] = React.useState<
    | { state: 'loading' }
    | { state: 'ok'; data: KernelPackages }
    | { state: 'none'; message: string }
  >({ state: 'loading' })
  const [find, setFind] = React.useState('')
  React.useEffect(() => {
    if (!kernel) {
      setState({
        state: 'none',
        message: 'Start the kernel to see what it can import.',
      })
      return
    }
    let live = true
    getKernelPackages({ data: { env, kernel } }).then(
      (result) => {
        if (!live) return
        setState(
          result.state === 'ok'
            ? { state: 'ok', data: result.data }
            : {
                state: 'none',
                message:
                  'message' in result
                    ? result.message
                    : 'The kernel has stopped.',
              },
        )
      },
      () =>
        live &&
        setState({ state: 'none', message: 'Could not list packages.' }),
    )
    return () => {
      live = false
    }
  }, [env, kernel])
  if (state.state === 'loading')
    return (
      <p className="flex items-center gap-2 text-xs text-muted-foreground">
        <Loader2Icon className="size-3 animate-spin" /> Reading the kernel
      </p>
    )
  if (state.state === 'none')
    return <p className="text-xs text-muted-foreground">{state.message}</p>
  const needle = find.trim().toLowerCase()
  const items = state.data.items
    .filter((item) => !needle || item.name.toLowerCase().includes(needle))
    .sort((a, b) =>
      a.source === b.source
        ? a.name.localeCompare(b.name)
        : a.source === 'notebook'
          ? -1
          : 1,
    )
  return (
    <div className="flex flex-col gap-2">
      <div className="relative">
        <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-faint" />
        <Input
          aria-label="Find an installed package"
          placeholder={`Find among ${state.data.items.length} packages`}
          value={find}
          onChange={(event) => setFind(event.target.value)}
          className="h-8 pl-8"
        />
      </div>
      <ul className="max-h-[200px] overflow-auto rounded-lg border border-line text-[12.5px]">
        {items.slice(0, 300).map((item) => (
          <li
            key={`${item.source}:${item.name}`}
            className="flex items-center gap-2 border-b border-line-soft px-3 py-1.5 last:border-b-0"
          >
            <span className="min-w-0 flex-1 truncate font-mono">
              {item.name}
            </span>
            <span className="font-mono text-[11.5px] text-muted-foreground">
              {item.version}
            </span>
            {item.source === 'notebook' ? (
              <Badge variant="outline" size="sm">
                This notebook
              </Badge>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  )
}

export function PackagesDialog({
  open,
  onOpenChange,
  env,
  kernel,
  packages,
  editable,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  env: string
  kernel: string | null
  packages: ReadonlyArray<string>
  editable: boolean
  /** Saves the list with the notebook; resolves to an error message or null */
  onSave: (packages: Array<string>) => Promise<string | null>
}) {
  const [text, setText] = React.useState(packages.join('\n'))
  const [error, setError] = React.useState<string | null>(null)
  const [busy, setBusy] = React.useState(false)
  React.useEffect(() => {
    if (open) {
      setText(packages.join('\n'))
      setError(null)
    }
  }, [open, packages])
  const next = parsePackages(text)
  const changed = next.join('\n') !== packages.join('\n')
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[560px]">
        <DialogHeader>
          <DialogTitle>Packages</DialogTitle>
          <DialogDescription>
            Requirements this notebook installs on top of the Phlo API&apos;s
            own Python, one per line, saved in the .ipynb file.
          </DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-3">
          <Textarea
            aria-label="Package requirements"
            rows={5}
            readOnly={!editable}
            value={text}
            placeholder={'scikit-learn>=1.5\nstatsmodels'}
            onChange={(event) => {
              setText(event.target.value)
              setError(null)
            }}
            className="font-mono text-[12.5px]"
          />
          <p className="text-xs text-muted-foreground">
            Names and version ranges only: no URLs or pip options. Each set of
            packages gets its own environment, built when the kernel starts. An
            operator allows this with PHLO_NOTEBOOK_PACKAGES=1 and can point it
            at a private index.
          </p>
          {error ? (
            <p role="alert" className="text-xs text-bad-text">
              {error}
            </p>
          ) : null}
          <div className="border-t border-line-soft pt-3">
            <div className="pb-2 text-[11px] font-medium tracking-wide text-faint uppercase">
              In the running kernel
            </div>
            {open ? <Installed env={env} kernel={kernel} /> : null}
          </div>
        </DialogBody>
        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Close
          </Button>
          {editable ? (
            <Button
              disabled={!changed || busy}
              onClick={async () => {
                setBusy(true)
                const failure = await onSave(next)
                setBusy(false)
                if (failure) setError(failure)
                else onOpenChange(false)
              }}
            >
              {busy ? 'Saving…' : 'Save packages'}
            </Button>
          ) : null}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
