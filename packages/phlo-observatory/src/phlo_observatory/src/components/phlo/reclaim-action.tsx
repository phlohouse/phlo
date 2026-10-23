/** Reclamation requires explicit identity confirmation and never bypasses server safety checks. */
import * as React from 'react'
import { ActionButton } from './action-button'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function ReclaimAction({
  identity,
  label,
  description,
  operation,
  disabled,
  onReclaim,
}: {
  identity: string
  label: string
  description: string
  operation: string
  disabled?: boolean
  onReclaim: (reason: string) => Promise<void>
}) {
  const [open, setOpen] = React.useState(false)
  const [confirmation, setConfirmation] = React.useState('')
  const [reason, setReason] = React.useState('')
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  async function reclaim() {
    setBusy(true)
    setError('')
    try {
      await onReclaim(reason.trim())
      setOpen(false)
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'Reclamation was not confirmed. Refresh before retrying.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <ActionButton
        operation={operation}
        variant="destructive"
        disabled={disabled || busy}
        onClick={() => {
          setConfirmation('')
          setReason('')
          setError('')
          setOpen(true)
        }}
      >
        {label}
      </ActionButton>
      <Dialog open={open} onOpenChange={(value) => !busy && setOpen(value)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>{label}</DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm">{description}</p>
            <p className="text-sm font-medium text-bad-text">
              This is permanent. Export anything you need before continuing.
              Restoration will no longer be available.
            </p>
            <p className="break-all font-mono text-xs">{identity}</p>
            <label className="grid gap-2 text-sm">
              Confirmation identity
              <input
                value={confirmation}
                onChange={(event) => setConfirmation(event.target.value)}
                autoComplete="off"
                className="min-w-0 rounded-md border border-line bg-background p-3"
              />
            </label>
            <p className="text-xs text-muted-foreground">
              Type the exact identity above. Authorization and active or
              unresolved operation checks still apply on the server. Archiving
              does not guarantee reclamation: configured minimum audit retention
              must also have elapsed.
            </p>
            <label className="grid gap-2 text-sm">
              Reason
              <textarea
                value={reason}
                maxLength={1000}
                onChange={(event) => setReason(event.target.value)}
                className="min-h-24 rounded-md border border-line bg-background p-3"
              />
            </label>
            {error ? (
              <p role="alert" className="text-sm text-bad-text">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              variant="outline"
              disabled={busy}
              onClick={() => setOpen(false)}
            >
              Cancel
            </Button>
            <ActionButton
              operation={operation}
              variant="destructive"
              disabled={busy || confirmation !== identity || !reason.trim()}
              onClick={() => void reclaim()}
            >
              {busy ? 'Reclaiming…' : 'Permanently reclaim'}
            </ActionButton>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
