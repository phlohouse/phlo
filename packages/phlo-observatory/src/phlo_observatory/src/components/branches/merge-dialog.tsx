/** Presents merge evidence and collects approval for a branch merge. */
import * as React from 'react'
import { GitMergeIcon } from 'lucide-react'
import type {
  BranchAction,
  BranchCheck,
  BranchRef,
} from '@/lib/data/api/branches'
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
import { Textarea } from '@/components/ui/input'
import { Mono } from '@/components/phlo/status'

export function MergeDialog({
  open,
  branch,
  target,
  busy,
  error,
  checks,
  trial,
  onClose,
  onChecks,
  onTrial,
  onMerge,
  onMessageChange,
}: {
  open: boolean
  branch: BranchRef
  target: BranchRef
  busy: boolean
  error?: string
  checks?: Array<BranchCheck>
  trial?: BranchAction
  onClose: () => void
  onChecks: () => void
  onTrial: (message: string) => void
  onMerge: (message: string, version: string) => void
  onMessageChange: () => void
}) {
  const [message, setMessage] = React.useState('')
  const version =
    trial?.status === 'succeeded' &&
    typeof trial.details.signature_target_version === 'string'
      ? trial.details.signature_target_version
      : undefined
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-[600px]">
        <DialogHeader>
          <DialogTitle>
            <GitMergeIcon className="mr-2 inline size-4" />
            Merge <Mono>{branch.name}</Mono> into <Mono>{target.name}</Mono>
          </DialogTitle>
        </DialogHeader>
        <DialogBody>
          <p className="m-0 text-sm text-muted-foreground">
            Pinned to {branch.hash.slice(0, 12)} → {target.hash.slice(0, 12)}.
            Reload if either head changes.
          </p>
          <div>
            {checks?.map((check) => (
              <p key={check.name} className="m-0 text-sm">
                <strong>{check.name}</strong>: {check.status}
                {check.run_id ? ` · ${check.run_id}` : ''}
                {check.message ? ` · ${check.message}` : ''}
              </p>
            ))}
          </div>
          <div className="flex flex-col gap-1.5">
            <Label htmlFor="merge-message">
              Merge message and signature justification
            </Label>
            <Textarea
              id="merge-message"
              value={message}
              onChange={(event) => {
                setMessage(event.target.value)
                onMessageChange()
              }}
              rows={3}
            />
          </div>
          {trial ? (
            <p className="m-0 text-sm">
              Trial: <strong>{trial.status}</strong>
              {Array.isArray(trial.details.conflicts) &&
              trial.details.conflicts.length
                ? ` · conflicts: ${trial.details.conflicts.join(', ')}`
                : ''}
            </p>
          ) : null}
          {error ? (
            <p className="m-0 text-sm text-bad-text" role="alert">
              {error}
            </p>
          ) : null}
        </DialogBody>
        <DialogFooter className="flex-wrap">
          <Button variant="outline" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="outline" disabled={busy} onClick={onChecks}>
            Run checks
          </Button>
          <Button
            variant="outline"
            disabled={busy || !message.trim()}
            onClick={() => onTrial(message.trim())}
          >
            Trial merge
          </Button>
          <Button
            disabled={busy || !message.trim() || !version}
            onClick={() => version && onMerge(message.trim(), version)}
          >
            Sign and merge
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
