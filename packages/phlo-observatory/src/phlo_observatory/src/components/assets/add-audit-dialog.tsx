/** Collects and validates a new asset audit proposal. */
import * as React from 'react'
import { z } from 'zod'
import type { Env } from '@/lib/data/types'
import type { AuditRule } from '@/lib/data/api/assets'
import { createAuditProposal } from '@/lib/data/api/assets'
import { Mono } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'

type State =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | {
      kind: 'created'
      id: string
      ref: string
      path: string
      digest: string
      source: string
      patch: string
    }

function auditBoundsValid(
  kind: AuditRule['kind'],
  minimum: string,
  maximum: string,
) {
  return (
    kind !== 'range' ||
    (minimum.trim() !== '' &&
      maximum.trim() !== '' &&
      Number.isFinite(Number(minimum)) &&
      Number.isFinite(Number(maximum)) &&
      Number(minimum) <= Number(maximum))
  )
}

function auditRequestValid(
  confirmed: boolean,
  name: string,
  column: string,
  boundsValid: boolean,
) {
  return confirmed && Boolean(name) && Boolean(column) && boundsValid
}

export function AddAuditDialog({
  open,
  onClose,
  assetId,
  env,
  columns,
}: {
  open: boolean
  onClose: () => void
  assetId: string
  env: Env
  columns: Array<{ name: string }>
}) {
  const [name, setName] = React.useState('')
  const [kind, setKind] = React.useState<AuditRule['kind']>('not_null')
  const [column, setColumn] = React.useState(columns[0]?.name ?? '')
  const [minimum, setMinimum] = React.useState('0')
  const [maximum, setMaximum] = React.useState('100')
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<State>({ kind: 'idle' })
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  const intent = `${name}:${kind}:${column}:${minimum}:${maximum}`
  const storageKey = `phlo:audit-proposal:${env}:${assetId}:${intent}`
  const boundsValid = auditBoundsValid(kind, minimum, maximum)
  const requestValid = auditRequestValid(confirmed, name, column, boundsValid)
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (submitting.current || state.kind === 'created' || !requestValid) return
    const rule: AuditRule =
      kind === 'range'
        ? { kind, column, minimum: Number(minimum), maximum: Number(maximum) }
        : { kind, column }
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      const proposal = await createAuditProposal({
        data: {
          env,
          id: assetId,
          check_name: name,
          rules: [rule],
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({
        kind: 'created',
        id: proposal.proposal_id,
        ref: proposal.nessie_ref,
        path: proposal.file_path,
        digest: proposal.source_digest,
        source: proposal.source,
        patch: proposal.patch,
      })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error
            ? error.message
            : 'Audit proposal request failed.',
      })
    } finally {
      submitting.current = false
    }
  }
  const locked =
    key.current !== null || state.kind === 'pending' || state.kind === 'created'
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && state.kind !== 'pending') onClose()
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => void submit(event)}
          className="flex min-h-0 flex-col"
        >
          <DialogHeader>
            <DialogTitle>Add audit proposal</DialogTitle>
            <DialogDescription>
              <Mono>{assetId}</Mono> · pinned to {env}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <label className="flex flex-col gap-2 text-sm">
              Check name
              <Input
                required
                pattern="[A-Za-z][A-Za-z0-9_]*"
                maxLength={64}
                value={name}
                disabled={locked}
                onChange={(e) => setName(e.target.value)}
                placeholder="orders_are_valid"
              />
            </label>
            <label className="flex flex-col gap-2 text-sm">
              Rule
              <select
                value={kind}
                disabled={locked}
                onChange={(e) =>
                  setKind(
                    z
                      .enum(['not_null', 'unique', 'range'])
                      .parse(e.target.value),
                  )
                }
                className="h-10 rounded-lg border border-border bg-card px-3"
              >
                <option value="not_null">Not null</option>
                <option value="unique">Unique</option>
                <option value="range">Range</option>
              </select>
            </label>
            <label className="flex flex-col gap-2 text-sm">
              Observed column
              <select
                value={column}
                disabled={locked}
                onChange={(e) => setColumn(e.target.value)}
                className="h-10 rounded-lg border border-border bg-card px-3 font-mono"
              >
                {columns.map((item) => (
                  <option key={item.name}>{item.name}</option>
                ))}
              </select>
            </label>
            {kind === 'range' ? (
              <div className="grid grid-cols-2 gap-3">
                <label className="flex flex-col gap-2 text-sm">
                  Minimum
                  <Input
                    type="number"
                    step="any"
                    value={minimum}
                    disabled={locked}
                    onChange={(e) => setMinimum(e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-2 text-sm">
                  Maximum
                  <Input
                    type="number"
                    step="any"
                    value={maximum}
                    disabled={locked}
                    onChange={(e) => setMaximum(e.target.value)}
                  />
                </label>
              </div>
            ) : null}
            {!boundsValid ? (
              <div role="alert" className="text-sm text-bad-text">
                Minimum must not exceed maximum.
              </div>
            ) : null}
            <p className="m-0 text-sm text-muted-foreground">
              This stores source and a patch locally for human review. It does
              not run the audit or change project code.
            </p>
            <label className="flex items-start gap-2 text-sm">
              <input
                type="checkbox"
                checked={confirmed}
                disabled={locked}
                onChange={(e) => setConfirmed(e.target.checked)}
                className="mt-1"
              />
              I confirm this local proposal in {env}.
            </label>
            {state.kind === 'failed' ? (
              <div role="alert" className="text-sm text-bad-text">
                {state.message} Retry reuses the same operation key.
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    key.current = null
                    setConfirmed(false)
                    setState({ kind: 'idle' })
                  }}
                >
                  Edit request
                </Button>
              </div>
            ) : null}
            {state.kind === 'created' ? (
              <div role="status" className="flex flex-col gap-2 text-sm">
                <div>
                  Proposal {state.id} stored on ref {state.ref}.
                </div>
                <div>
                  Path: <Mono className="break-all">{state.path}</Mono>
                </div>
                <div>
                  Digest: <Mono className="break-all">{state.digest}</Mono>
                </div>
                <details>
                  <summary>Generated source</summary>
                  <pre className="overflow-auto rounded-lg bg-sunken p-3 text-xs">
                    {state.source}
                  </pre>
                </details>
                <details>
                  <summary>Patch</summary>
                  <pre className="overflow-auto rounded-lg bg-sunken p-3 text-xs">
                    {state.patch}
                  </pre>
                </details>
                <Button
                  type="button"
                  disabled
                  title="Publishing a GitHub pull request is not authorised."
                >
                  Publish GitHub PR unavailable
                </Button>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    sessionStorage.removeItem(storageKey)
                    key.current = null
                    setConfirmed(false)
                    setState({ kind: 'idle' })
                  }}
                >
                  New proposal
                </Button>
              </div>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={state.kind === 'pending'}
              onClick={onClose}
              className="ml-auto"
            >
              Close
            </Button>
            <Button
              type="submit"
              disabled={
                !requestValid ||
                state.kind === 'pending' ||
                state.kind === 'created'
              }
            >
              {state.kind === 'pending'
                ? 'Storing…'
                : state.kind === 'failed'
                  ? 'Retry request'
                  : 'Store proposal'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
