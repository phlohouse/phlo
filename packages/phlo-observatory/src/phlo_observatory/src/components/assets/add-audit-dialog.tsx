/** Collects and validates a new asset audit proposal. */
import * as React from 'react'
import type { Env } from '@/lib/data/types'
import type {
  AuditProposal,
  AuditRule,
  AuditTestResult,
} from '@/lib/data/api/assets'
import {
  createAuditProposal,
  getAuditProposal,
  publishAuditProposal,
  testAuditProposal,
} from '@/lib/data/api/assets'
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
      proposal: AuditProposal
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
      <div className="flex flex-col gap-1.5">
        <span id="aa-type" className="text-[13.5px] font-medium">
          Check
        </span>
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
            aria-labelledby="aa-type"
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
      </div>
      <div
        className={
          kind === 'range'
            ? 'grid grid-cols-2 gap-3 sm:grid-cols-[1.4fr_1fr_1fr]'
            : ''
        }
      >
        <Field className={kind === 'range' ? 'col-span-2 sm:col-span-1' : ''}>
          <FieldLabel>Column</FieldLabel>
          {locked ? (
            <div className="flex h-9 items-center rounded-lg border border-input bg-card px-3 opacity-50">
              <Mono>{column}</Mono>
            </div>
          ) : (
            <Select
              aria-label="Column"
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
          <>
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
          </>
        ) : null}
      </div>
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
  const [kind, setKind] = React.useState<AuditRule['kind']>('range')
  const [column, setColumn] = React.useState(columns[0]?.name ?? '')
  const [minimum, setMinimum] = React.useState('0')
  const [maximum, setMaximum] = React.useState('100')
  const [policy, setPolicy] = React.useState<'block' | 'warn'>('block')
  const [confirmed, setConfirmed] = React.useState(false)
  const [state, setState] = React.useState<State>({ kind: 'idle' })
  const [operation, setOperation] = React.useState<
    'test' | 'publish' | 'detail' | null
  >(null)
  const [operationError, setOperationError] = React.useState('')
  const [testResult, setTestResult] = React.useState<AuditTestResult | null>(
    null,
  )
  const [pullRequest, setPullRequest] = React.useState<string | null>(null)
  const [publishConfirmed, setPublishConfirmed] = React.useState(false)
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  const busy = React.useRef(false)
  const intent = `${name}:${kind}:${column}:${minimum}:${maximum}:${policy}`
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
          failure_policy: policy,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({
        kind: 'created',
        proposal,
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
  async function runOperation(action: 'test' | 'publish' | 'detail') {
    if (state.kind !== 'created' || busy.current) return
    busy.current = true
    setOperation(action)
    setOperationError('')
    const data = { env, id: assetId, proposal_id: state.proposal.proposal_id }
    try {
      if (action === 'test') {
        setTestResult(null)
        setTestResult(await testAuditProposal({ data }))
      } else if (action === 'detail') {
        setState({
          kind: 'created',
          proposal: await getAuditProposal({ data }),
        })
      } else {
        const publishKey = `phlo:audit-publish:${env}:${assetId}:${data.proposal_id}`
        const idempotencyKey =
          sessionStorage.getItem(publishKey) ?? crypto.randomUUID()
        sessionStorage.setItem(publishKey, idempotencyKey)
        const published = await publishAuditProposal({
          data: {
            ...data,
            expected_source_digest: state.proposal.source_digest,
            idempotency_key: idempotencyKey,
            confirmed: true,
          },
        })
        setPullRequest(published.pull_request_url)
      }
    } catch (error) {
      setOperationError(
        error instanceof Error ? error.message : 'Audit operation failed.',
      )
    } finally {
      busy.current = false
      setOperation(null)
    }
  }
  const locked =
    key.current !== null || state.kind === 'pending' || state.kind === 'created'
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && state.kind !== 'pending' && operation === null) onClose()
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => void submit(event)}
          className="flex min-h-0 flex-col"
        >
          <DialogHeader>
            <DialogTitle>Add audit</DialogTitle>
            <DialogDescription>
              <Mono>{assetId}</Mono> · pinned to {env}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <div className={state.kind === 'created' ? 'hidden' : 'contents'}>
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
              <div className="flex flex-col gap-1.5">
                <span id="aa-fail" className="text-[13.5px] font-medium">
                  When it fails
                </span>
                <RadioGroup
                  aria-labelledby="aa-fail"
                  disabled={locked}
                  value={policy}
                  onValueChange={(value) => {
                    if (!locked && (value === 'block' || value === 'warn'))
                      setPolicy(value)
                  }}
                >
                  <ChoiceItem value="block">Block downstream</ChoiceItem>
                  <ChoiceItem value="warn">Warn only</ChoiceItem>
                </RadioGroup>
                <span className="text-[12.5px] leading-snug text-muted-foreground">
                  {policy === 'block'
                    ? 'After review and activation, a failing check blocks downstream assets.'
                    : 'After review and activation, failures emit warnings and data still flows.'}{' '}
                  Incident notification is not configured by this dialog.
                </span>
              </div>
              <p className="m-0 text-[12.5px] leading-snug text-muted-foreground">
                This stores source and a patch locally for human review. It does
                not install the check. Test the proposed rules against up to 100
                real rows after creation.
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
            </div>
            {state.kind === 'created' ? (
              <AuditProposalReview
                proposal={state.proposal}
                operation={operation}
                operationError={operationError}
                testResult={testResult}
                pullRequest={pullRequest}
                publishConfirmed={publishConfirmed}
                onPublishConfirmed={setPublishConfirmed}
                onOperation={runOperation}
                onNew={() => {
                  sessionStorage.removeItem(storageKey)
                  key.current = null
                  setConfirmed(false)
                  setTestResult(null)
                  setOperationError('')
                  setPullRequest(null)
                  setPublishConfirmed(false)
                  setState({ kind: 'idle' })
                }}
              />
            ) : null}
          </DialogBody>
          <DialogFooter className="flex-wrap">
            <Button
              type="button"
              variant="outline"
              disabled={state.kind === 'pending' || operation !== null}
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
                  : 'Create proposal'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function AuditProposalReview({
  proposal,
  operation,
  operationError,
  testResult,
  pullRequest,
  publishConfirmed,
  onPublishConfirmed,
  onOperation,
  onNew,
}: {
  proposal: AuditProposal
  operation: 'test' | 'publish' | 'detail' | null
  operationError: string
  testResult: AuditTestResult | null
  pullRequest: string | null
  publishConfirmed: boolean
  onPublishConfirmed: (confirmed: boolean) => void
  onOperation: (action: 'test' | 'publish' | 'detail') => Promise<void>
  onNew: () => void
}) {
  return (
    <div role="status" className="flex flex-col gap-2 text-sm">
      <div className="rounded-[10px] border border-border bg-raised p-3">
        Proposal created on ref {proposal.nessie_ref}. Not installed or active.
        Policy after activation:{' '}
        {proposal.failure_policy === 'block' ? 'Block downstream' : 'Warn only'}
        .
      </div>
      <div>
        Path: <Mono className="break-all">{proposal.file_path}</Mono>
      </div>
      <div>
        Digest: <Mono className="break-all">{proposal.source_digest}</Mono>
      </div>
      <details>
        <summary>Generated source</summary>
        <pre className="overflow-auto rounded-lg bg-sunken p-3 text-xs">
          {proposal.source}
        </pre>
      </details>
      <details>
        <summary>Patch</summary>
        <pre className="overflow-auto rounded-lg bg-sunken p-3 text-xs">
          {proposal.patch}
        </pre>
      </details>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={operation !== null}
          onClick={() => void onOperation('detail')}
        >
          Refresh proposal
        </Button>
        <Button
          type="button"
          variant="outline"
          disabled={operation !== null}
          onClick={() => void onOperation('test')}
        >
          {operation === 'test' ? 'Testing…' : 'Run dry-run'}
        </Button>
      </div>
      {testResult ? <AuditTestEvidence result={testResult} /> : null}
      <CheckLine
        checked={publishConfirmed}
        disabled={operation !== null || pullRequest !== null}
        onCheckedChange={onPublishConfirmed}
      >
        Publish this source as a draft GitHub PR for human review.
      </CheckLine>
      <Button
        type="button"
        disabled={
          !publishConfirmed || operation !== null || pullRequest !== null
        }
        onClick={() => void onOperation('publish')}
      >
        {operation === 'publish' ? 'Publishing…' : 'Publish GitHub PR'}
      </Button>
      {pullRequest ? (
        <a href={pullRequest} target="_blank" rel="noreferrer">
          Open draft pull request
        </a>
      ) : null}
      <p className="m-0 text-xs text-muted-foreground">
        Installation requires reviewed merge and Dagster code-location reload.
        This dialog has not observed an installed check or a scheduled
        execution.
      </p>
      {operationError ? (
        <div role="alert" className="text-sm text-bad-text">
          {operationError} Publication retries reuse the same key.
        </div>
      ) : null}
      <Button
        type="button"
        variant="outline"
        disabled={operation !== null}
        onClick={onNew}
      >
        New proposal
      </Button>
    </div>
  )
}

function AuditTestEvidence({ result }: { result: AuditTestResult }) {
  return (
    <div
      className={
        result.passed
          ? 'rounded-[10px] border border-ok-line bg-ok-wash p-3 text-ok-ink'
          : 'rounded-[10px] border border-bad-line bg-bad-wash p-3 text-bad-ink'
      }
    >
      <div>
        {result.rows_checked === 0
          ? 'Dry-run skipped: empty relation.'
          : `Dry-run ${result.passed ? 'passed' : 'failed'} · ${result.rows_checked} rows checked.`}
      </div>
      <div className="text-xs">
        {result.sampled
          ? 'Truncated sample only. Whole-table validity and uniqueness are not proven.'
          : 'Full relation read within the 100-row budget.'}
      </div>
      <div className="text-xs break-all">
        Engine: {result.engine} · ref {result.nessie_ref} · {result.executed_at}
      </div>
      {result.results.map((rule, index) => (
        <div key={index} className="text-xs">
          {rule.column} · {rule.kind}: {rule.failure_count} failing rows
          {rule.failure_message ? ` · ${rule.failure_message}` : ''}
        </div>
      ))}
      <div className="mt-1 text-xs">
        Rules executed for this dry-run only. The check is not installed.
      </div>
    </div>
  )
}
