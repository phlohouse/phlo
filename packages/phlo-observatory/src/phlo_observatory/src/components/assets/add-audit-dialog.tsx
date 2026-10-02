/** Collects and validates a new asset audit proposal. */
import * as React from 'react'
import type { Env } from '@/lib/data/types'
import type { AuditRule } from '@/lib/data/api/assets'
import { createAuditProposal } from '@/lib/data/api/assets'
import { Mono } from '@/components/phlo/status'
import { Button } from '@/components/ui/button'
import { CheckLine, ChoiceItem, RadioGroup } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldDescription, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { Select } from '@/components/ui/select'

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

function AuditRuleFields({
  kind,
  setKind,
  column,
  setColumn,
  columns,
  minimum,
  setMinimum,
  maximum,
  setMaximum,
  locked,
  boundsValid,
}: {
  kind: AuditRule['kind']
  setKind: (kind: AuditRule['kind']) => void
  column: string
  setColumn: (column: string) => void
  columns: Array<{ name: string }>
  minimum: string
  setMinimum: (minimum: string) => void
  maximum: string
  setMaximum: (maximum: string) => void
  locked: boolean
  boundsValid: boolean
}) {
  return (
    <>
      <Field>
        <FieldLabel>Rule</FieldLabel>
        {locked ? (
          <div className="text-[13.5px] text-muted-foreground">
            {kind === 'not_null'
              ? 'Not null'
              : kind === 'unique'
                ? 'Unique'
                : 'Range'}
          </div>
        ) : (
          <RadioGroup
            value={kind}
            onValueChange={(value) => {
              if (
                value === 'not_null' ||
                value === 'unique' ||
                value === 'range'
              )
                setKind(value)
            }}
          >
            <ChoiceItem value="not_null">Not null</ChoiceItem>
            <ChoiceItem value="unique">Unique</ChoiceItem>
            <ChoiceItem value="range">Range</ChoiceItem>
          </RadioGroup>
        )}
      </Field>
      <Field>
        <FieldLabel>Observed column</FieldLabel>
        {locked ? (
          <div className="flex h-9 items-center rounded-lg border border-input bg-card px-3 opacity-50">
            <Mono>{column}</Mono>
          </div>
        ) : (
          <Select
            value={column}
            onValueChange={setColumn}
            className="font-mono text-[13px]"
            options={columns.map((item) => ({
              value: item.name,
              label: <Mono className="text-[13px]">{item.name}</Mono>,
            }))}
          />
        )}
      </Field>
      {kind === 'range' ? (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <Field>
            <FieldLabel>Minimum</FieldLabel>
            <Input
              type="number"
              step="any"
              value={minimum}
              disabled={locked}
              onChange={(e) => setMinimum(e.target.value)}
            />
          </Field>
          <Field>
            <FieldLabel>Maximum</FieldLabel>
            <Input
              type="number"
              step="any"
              value={maximum}
              disabled={locked}
              onChange={(e) => setMaximum(e.target.value)}
            />
          </Field>
        </div>
      ) : null}
      {!boundsValid ? (
        <div role="alert" className="text-sm text-bad-text">
          Minimum must not exceed maximum.
        </div>
      ) : null}
    </>
  )
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
            <Field>
              <FieldLabel>Check name</FieldLabel>
              <Input
                required
                pattern="[A-Za-z][A-Za-z0-9_]*"
                maxLength={64}
                value={name}
                disabled={locked}
                onChange={(e) => setName(e.target.value)}
                placeholder="orders_are_valid"
              />
              <FieldDescription>
                Starts with a letter; letters, numbers, and underscores only.
              </FieldDescription>
            </Field>
            <AuditRuleFields
              kind={kind}
              setKind={setKind}
              column={column}
              setColumn={setColumn}
              columns={columns}
              minimum={minimum}
              setMinimum={setMinimum}
              maximum={maximum}
              setMaximum={setMaximum}
              locked={locked}
              boundsValid={boundsValid}
            />
            <p className="m-0 text-[12.5px] leading-snug text-muted-foreground">
              This stores source and a patch locally for human review. It does
              not run the audit or change project code.
            </p>
            <CheckLine
              checked={confirmed}
              disabled={locked}
              onCheckedChange={setConfirmed}
            >
              I confirm this local proposal in {env}.
            </CheckLine>
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
          <DialogFooter className="flex-wrap">
            <Button
              type="button"
              variant="outline"
              disabled={state.kind === 'pending'}
              onClick={onClose}
              size="lg"
              className="ml-auto h-10 bg-card sm:h-9"
            >
              Close
            </Button>
            <Button
              type="submit"
              size="lg"
              className="h-10 sm:h-9"
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
