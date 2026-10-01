/** Collects and submits the details for a new branch. */
import * as React from 'react'
import { GitBranchIcon } from 'lucide-react'
import type { BranchRef } from '@/lib/data/api/branches'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Label } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'
import { Mono } from '@/components/phlo/status'

export function NewBranchDialog({
  open,
  env,
  refs,
  busy,
  error,
  onClose,
  onCreate,
}: {
  open: boolean
  env: 'prod' | 'staging'
  refs: Array<BranchRef>
  busy: boolean
  error?: string
  onClose: () => void
  onCreate: (name: string, fromRef: string) => void
}) {
  const [suffix, setSuffix] = React.useState('')
  const [fromRef, setFromRef] = React.useState(refs[0]?.name ?? '')
  const name = `${env}-${suffix}`
  const valid =
    /^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$/.test(name) && suffix.length > 0
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-[560px]">
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (valid && fromRef) onCreate(name, fromRef)
          }}
        >
          <DialogHeader className="flex-row items-center gap-2.5">
            <GitBranchIcon className="size-[18px] text-branch" />
            <DialogTitle>New branch</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="branch-name">Name</Label>
              <div className="flex items-center">
                <Mono className="rounded-l-lg border border-r-0 border-input bg-raised px-3 py-2">
                  {env}-
                </Mono>
                <Input
                  id="branch-name"
                  value={suffix}
                  onChange={(event) => setSuffix(event.target.value)}
                  required
                  pattern="[A-Za-z0-9][A-Za-z0-9._-]*"
                  className="rounded-l-none font-mono"
                />
              </div>
              <span className="text-xs text-muted-foreground">
                Environment namespace is required by the API.
              </span>
            </div>
            <div className="flex flex-col gap-1.5">
              <Label htmlFor="branch-from">Start from</Label>
              <Select
                id="branch-from"
                value={fromRef}
                onValueChange={setFromRef}
                options={refs.map((ref) => ({
                  value: ref.name,
                  label: `${ref.name} @ ${ref.hash.slice(0, 8)}`,
                }))}
              />
            </div>
            {error ? (
              <p className="m-0 text-sm text-bad-text" role="alert">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              Cancel
            </Button>
            <Button type="submit" disabled={!valid || !fromRef || busy}>
              {busy ? 'Creating…' : 'Create branch'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
