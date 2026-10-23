/** Reversible draft housekeeping and explicit, descriptive export for external review. */
import * as React from 'react'
import {
  changeMetricLifecycle,
  exportMetricDraft,
  reclaimMetricDraft,
} from '@/lib/data/api/metrics'
import {
  changeProductLifecycle,
  exportProductDraft,
  reclaimProductDraft,
} from '@/lib/data/api/data-products'
import { ActionButton as Button } from '@/components/phlo/action-button'
import { ReclaimAction } from '@/components/phlo/reclaim-action'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'

export function DraftLifecycleActions({
  kind,
  env,
  id,
  version,
  status,
  onChanged,
  onReclaimed,
}: {
  kind: 'metric' | 'product'
  env: string
  id: string
  version: number
  status: 'draft' | 'archived'
  onChanged: () => void
  onReclaimed: () => void
}) {
  const [open, setOpen] = React.useState(false)
  const [busy, setBusy] = React.useState(false)
  const [error, setError] = React.useState('')
  const [downloaded, setDownloaded] = React.useState(false)
  const action = status === 'archived' ? 'restore' : 'archive'
  const prefix = kind === 'metric' ? 'v1_metric_model' : 'v1_data_product'
  async function change() {
    setBusy(true)
    setError('')
    try {
      const request = {
        data: { env, id, action, expected_version: version },
      } as const
      await (kind === 'metric'
        ? changeMetricLifecycle(request)
        : changeProductLifecycle(request))
      setOpen(false)
      onChanged()
    } catch (cause) {
      setError(
        cause instanceof Error
          ? cause.message
          : 'The draft was not changed. Reload before retrying.',
      )
    } finally {
      setBusy(false)
    }
  }
  async function download() {
    setBusy(true)
    setError('')
    setDownloaded(false)
    try {
      const request = { data: { env, id } }
      const result = await (kind === 'metric'
        ? exportMetricDraft(request)
        : exportProductDraft(request))
      const url = URL.createObjectURL(
        new Blob([JSON.stringify(result, null, 2)], {
          type: 'application/json',
        }),
      )
      const link = document.createElement('a')
      link.href = url
      link.download = `${kind}-${id}-v${result.version}.json`
      link.click()
      URL.revokeObjectURL(url)
      setDownloaded(true)
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Could not export this draft.',
      )
    } finally {
      setBusy(false)
    }
  }
  return (
    <>
      <Button
        operation={`${prefix}_export`}
        variant="outline"
        disabled={busy}
        onClick={() => void download()}
      >
        Export draft JSON
      </Button>
      <Button
        operation={`${prefix}_${action}`}
        variant="outline"
        disabled={busy}
        onClick={() => {
          setError('')
          setOpen(true)
        }}
      >
        {status === 'archived' ? 'Restore draft' : 'Archive draft'}
      </Button>
      {status === 'archived' ? (
        <ReclaimAction
          identity={id}
          label="Reclaim draft definition"
          description="Permanently remove this archived definition and free one retained-definition slot. A compact identity tombstone remains and this ID can never be reused. This does not delete datasets or provider data, or promise filesystem vacuum."
          operation={`${prefix}_reclaim`}
          disabled={busy}
          onReclaim={async (reason) => {
            const request = {
              data: {
                env,
                id,
                expected_version: version,
                confirmed: true as const,
                reason,
              },
            }
            await (kind === 'metric'
              ? reclaimMetricDraft(request)
              : reclaimProductDraft(request))
            onReclaimed()
          }}
        />
      ) : null}
      {downloaded ? (
        <span role="status" className="max-w-sm text-xs text-muted-foreground">
          Definition exported for external review. No data, access grants or
          publication included.
        </span>
      ) : null}
      {error && !open ? (
        <p role="alert" className="max-w-sm text-xs text-bad-text">
          {error}
        </p>
      ) : null}
      <Dialog open={open} onOpenChange={(value) => !busy && setOpen(value)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>
              {status === 'archived' ? 'Restore draft' : 'Archive draft'} · {id}
            </DialogTitle>
          </DialogHeader>
          <DialogBody>
            <p className="text-sm">
              {status === 'archived'
                ? 'Restore this definition to the active draft inventory. Its source and policy checks still apply; this does not publish it.'
                : 'Remove this definition from the active inventory without deleting its metadata. You can inspect, export and restore the archived definition.'}
            </p>
            <p className="text-xs text-muted-foreground">
              Expected version {version}. A concurrent edit or lifecycle change
              will reject this request rather than overwrite it.
            </p>
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
            <Button
              operation={`${prefix}_${action}`}
              disabled={busy}
              onClick={() => void change()}
            >
              {busy
                ? 'Applying…'
                : status === 'archived'
                  ? 'Confirm restore'
                  : 'Confirm archive'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
