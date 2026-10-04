/** Plans and submits environment-pinned asset operations through their owner. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import type { Env } from '@/lib/data/types'
import type {
  MaterializationEstimate,
  MaterializationInput,
} from '@/lib/data/api/assets'
import type { BranchRef } from '@/lib/data/api/branches'
import {
  getMaterializationEstimate,
  materializationInputSchema,
  materializeAsset,
} from '@/lib/data/api/assets'
import { createBranch, getBranchesPage } from '@/lib/data/api/branches'
import { NewBranchDialog } from '@/components/branches/new-branch-dialog'
import { Button } from '@/components/ui/button'
import { CheckLine, OptionCard, RadioGroup } from '@/components/ui/checkbox'
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
import { Segmented } from '@/components/ui/toggle-group'
import { Stat } from '@/components/phlo/kpi'
import { Mono } from '@/components/phlo/status'

type State =
  | { kind: 'idle' }
  | { kind: 'pending' }
  | { kind: 'failed'; message: string }
  | { kind: 'accepted'; response: Awaited<ReturnType<typeof materializeAsset>> }
type EstimateState =
  | { kind: 'idle' }
  | { kind: 'pending'; intent: string }
  | { kind: 'failed'; intent: string; message: string }
  | { kind: 'ready'; intent: string; value: MaterializationEstimate }

type MaterializeDialogProps = {
  open: boolean
  onClose: () => void
  assetId: string
  env: Env
  jobs: Array<string>
  initialMode?: MaterializationInput['mode']
  onExplicitPartitions?: () => void
}

function confirmedPlan(
  inputValid: boolean,
  ready: MaterializationEstimate | null,
  confirmed: boolean,
  mode: MaterializationInput['mode'],
  destructive: string,
  assetId: string,
) {
  return (
    inputValid &&
    Boolean(ready?.plan_hash) &&
    confirmed &&
    (mode !== 'full' || destructive === assetId)
  )
}

function useMaterialization({
  open,
  assetId,
  env,
  initialMode = 'latest',
}: MaterializeDialogProps) {
  const [job, setJob] = React.useState('')
  const [mode, setMode] = React.useState(initialMode)
  const [from, setFrom] = React.useState('')
  const [to, setTo] = React.useState('')
  const [refs, setRefs] = React.useState<Array<BranchRef>>([])
  const [refError, setRefError] = React.useState<string | null>(null)
  const [target, setTarget] = React.useState('')
  const [rebuild, setRebuild] = React.useState(true)
  const [confirmed, setConfirmed] = React.useState(false)
  const [destructive, setDestructive] = React.useState('')
  const [state, setState] = React.useState<State>({ kind: 'idle' })
  const [branchOpen, setBranchOpen] = React.useState(false)
  const [branchBusy, setBranchBusy] = React.useState(false)
  const [branchError, setBranchError] = React.useState<string>()
  const branchRequest = React.useRef<{ intent: string; key: string } | null>(
    null,
  )
  const creatingBranch = React.useRef(false)
  const [refRevision, setRefRevision] = React.useState(0)
  const [autoPlanTarget, setAutoPlanTarget] = React.useState<string | null>(
    null,
  )
  const [estimate, setEstimate] = React.useState<EstimateState>({
    kind: 'idle',
  })
  const key = React.useRef<string | null>(null)
  const submitting = React.useRef(false)
  React.useEffect(() => {
    if (open) setMode(initialMode)
  }, [initialMode, open])
  React.useEffect(() => {
    if (!open) return
    let active = true
    void getBranchesPage({ data: { env } })
      .then((page) => {
        if (!active) return
        setRefs(page.branches)
        setTarget(
          (value) =>
            value ||
            page.branches.find((ref) => !ref.protected)?.name ||
            page.branches[0]?.name ||
            '',
        )
        setRefError(null)
      })
      .catch((error: unknown) => {
        if (active)
          setRefError(
            error instanceof Error
              ? error.message
              : 'Write references unavailable.',
          )
      })
    return () => {
      active = false
    }
  }, [open, env])
  const input = materializationInputSchema.safeParse({
    env,
    id: assetId,
    job_name: job || undefined,
    mode,
    write_ref: target || undefined,
    rebuild_downstream: rebuild,
    ...(mode === 'backfill'
      ? {
          from_time: from ? `${from}:00Z` : undefined,
          to_time: to ? `${to}:00Z` : undefined,
        }
      : {}),
  })
  const intent = JSON.stringify([
    input.success ? input.data : { job, mode, target, rebuild, from, to },
    refRevision,
  ])
  const intentRef = React.useRef(intent)
  intentRef.current = intent
  const ready =
    estimate.kind === 'ready' && estimate.intent === intent
      ? estimate.value
      : null
  const locked =
    branchBusy ||
    key.current !== null ||
    state.kind === 'pending' ||
    state.kind === 'accepted'
  const confirmationIntent = React.useRef(intent)
  React.useEffect(() => {
    if (confirmationIntent.current !== intent) {
      confirmationIntent.current = intent
      setConfirmed(false)
      setDestructive('')
    }
  }, [intent])
  const storageKey = `phlo:materialize:${env}:${assetId}:${ready?.plan_hash ?? ''}`
  async function refreshEstimate() {
    if (!input.success || !target) return
    setConfirmed(false)
    setDestructive('')
    setEstimate({ kind: 'pending', intent })
    try {
      const value = await getMaterializationEstimate({ data: input.data })
      if (intentRef.current === intent)
        setEstimate({ kind: 'ready', intent, value })
    } catch (error) {
      if (intentRef.current === intent)
        setEstimate({
          kind: 'failed',
          intent,
          message:
            error instanceof Error ? error.message : 'Estimate unavailable.',
        })
    }
  }
  React.useEffect(() => {
    if (autoPlanTarget !== target || !input.success) return
    setAutoPlanTarget(null)
    void refreshEstimate()
  })
  async function addBranch(name: string, fromRef: string) {
    if (creatingBranch.current || locked) return
    creatingBranch.current = true
    setBranchBusy(true)
    setBranchError(undefined)
    setConfirmed(false)
    setDestructive('')
    setEstimate({ kind: 'idle' })
    setRefRevision((value) => value + 1)
    try {
      const branchIntent = JSON.stringify({ env, name, fromRef })
      if (branchRequest.current?.intent !== branchIntent)
        branchRequest.current = {
          intent: branchIntent,
          key: crypto.randomUUID(),
        }
      const result = await createBranch({
        data: {
          env,
          name,
          fromRef,
          confirmed: true,
          idempotencyKey: branchRequest.current.key,
        },
      })
      if (result.status !== 'succeeded')
        throw new Error(
          'Branch creation was rejected or conflicted. Refresh the source and retry.',
        )
      const page = await getBranchesPage({ data: { env } })
      const created = page.branches.find((ref) => ref.name === result.branch)
      if (!created)
        throw new Error(
          'The created branch is not visible yet. Retry to reload references.',
        )
      setRefs(page.branches)
      setRefError(null)
      setTarget(created.name)
      setRefRevision((value) => value + 1)
      setAutoPlanTarget(created.name)
      setBranchOpen(false)
      branchRequest.current = null
    } catch (error) {
      setBranchError(
        error instanceof Error ? error.message : 'Branch creation failed.',
      )
    } finally {
      creatingBranch.current = false
      setBranchBusy(false)
    }
  }
  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (
      submitting.current ||
      state.kind === 'accepted' ||
      !input.success ||
      !ready?.plan_hash ||
      !confirmed ||
      (mode === 'full' && destructive !== assetId)
    )
      return
    submitting.current = true
    setState({ kind: 'pending' })
    try {
      key.current ??= sessionStorage.getItem(storageKey) ?? crypto.randomUUID()
      sessionStorage.setItem(storageKey, key.current)
      const response = await materializeAsset({
        data: {
          ...input.data,
          plan_hash: ready.plan_hash,
          idempotency_key: key.current,
          confirmed: true,
        },
      })
      setState({ kind: 'accepted', response })
    } catch (error) {
      setState({
        kind: 'failed',
        message:
          error instanceof Error
            ? error.message
            : 'Materialization request failed.',
      })
    } finally {
      submitting.current = false
    }
  }
  function reset(removeKey: boolean) {
    if (removeKey) sessionStorage.removeItem(storageKey)
    key.current = null
    setConfirmed(false)
    setEstimate({ kind: 'idle' })
    setState({ kind: 'idle' })
  }
  return {
    job,
    setJob,
    mode,
    setMode,
    from,
    setFrom,
    to,
    setTo,
    refs,
    refError,
    target,
    setTarget,
    rebuild,
    setRebuild,
    confirmed,
    setConfirmed,
    destructive,
    setDestructive,
    state,
    branchOpen,
    setBranchOpen,
    branchBusy,
    branchError,
    addBranch,
    estimate,
    ready,
    locked,
    intent,
    inputValid: input.success,
    canSubmit: confirmedPlan(
      input.success,
      ready,
      confirmed,
      mode,
      destructive,
      assetId,
    ),
    refreshEstimate,
    submit,
    reset,
  }
}

type Materialization = ReturnType<typeof useMaterialization>

function LoadingFields({
  operation: op,
  jobs,
  onExplicitPartitions,
}: {
  operation: Materialization
  jobs: Array<string>
  onExplicitPartitions?: () => void
}) {
  const namedJobs = jobs.filter((name) => !name.startsWith('__ASSET_JOB'))
  return (
    <>
      {namedJobs.length ? (
        <Field>
          <FieldLabel htmlFor="mz-job">Execution</FieldLabel>
          {op.locked ? (
            <Mono>{op.job || 'Automatic asset run'}</Mono>
          ) : (
            <Select
              id="mz-job"
              value={op.job}
              onValueChange={op.setJob}
              options={[
                { value: '', label: 'Automatic asset run' },
                ...namedJobs.map((name) => ({ value: name, label: name })),
              ]}
            />
          )}
          <FieldDescription>
            Choose a named job when its configuration matters. Automatic runs
            use the selected assets' default configuration.
          </FieldDescription>
        </Field>
      ) : null}
      <div className="flex flex-col gap-1.5">
        <span id="mz-mode" className="text-[13.5px] font-medium">
          What to load
        </span>
        <RadioGroup
          aria-labelledby="mz-mode"
          value={op.mode}
          disabled={op.locked}
          onValueChange={(value) => {
            if (value === 'latest' || value === 'backfill' || value === 'full')
              op.setMode(value)
          }}
          className="flex-col flex-nowrap"
        >
          <OptionCard
            value="latest"
            title="Next increment only"
            hint="Runs the latest available partition, or the source's configured incremental load."
          />
          <OptionCard
            value="backfill"
            title="Backfill a time range"
            hint="Reloads available time-window partitions using their Dagster configuration."
          />
          <OptionCard
            value="full"
            title="Full refresh"
            hint="Rebuilds a dbt table on a work branch. Requires confirmation and recent MFA."
          />
        </RadioGroup>
      </div>
      {op.mode === 'backfill' ? (
        <>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Field>
              <FieldLabel htmlFor="mz-from">From (UTC)</FieldLabel>
              <Input
                id="mz-from"
                type="datetime-local"
                required
                disabled={op.locked}
                value={op.from}
                onChange={(event) => op.setFrom(event.target.value)}
                className="min-w-0 font-mono text-[13.5px]"
              />
            </Field>
            <Field>
              <FieldLabel htmlFor="mz-to">To (UTC, exclusive)</FieldLabel>
              <Input
                id="mz-to"
                type="datetime-local"
                required
                disabled={op.locked}
                value={op.to}
                onChange={(event) => op.setTo(event.target.value)}
                className="min-w-0 font-mono text-[13.5px]"
              />
            </Field>
          </div>
          {onExplicitPartitions ? (
            <Button
              type="button"
              variant="outline"
              disabled={op.locked}
              onClick={onExplicitPartitions}
            >
              Use explicit partition keys
            </Button>
          ) : null}
        </>
      ) : null}
    </>
  )
}

function WriteTargetField({
  operation: op,
  env,
}: {
  operation: Materialization
  env: Env
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <span id="mz-target" className="text-[13.5px] font-medium">
        Write to
      </span>
      {op.locked ? (
        <Mono>{op.target}</Mono>
      ) : op.refs.length <= 3 ? (
        <Segmented
          aria-label="Write to"
          value={op.target}
          onValueChange={op.setTarget}
          className="self-start max-w-full flex-wrap"
          options={op.refs.map((ref) => ({
            value: ref.name,
            label: <Mono className="text-[12.5px]">{ref.name}</Mono>,
          }))}
        />
      ) : (
        <Select
          aria-label="Write to"
          value={op.target}
          onValueChange={op.setTarget}
          options={op.refs.map((ref) => ({ value: ref.name, label: ref.name }))}
        />
      )}
      <Button
        type="button"
        variant="outline"
        disabled={op.locked || !op.refs.length}
        onClick={() => op.setBranchOpen(true)}
        className="self-start"
      >
        Create work branch
      </Button>
      <p className="m-0 text-[12.5px] leading-snug text-muted-foreground">
        Only existing branches in {env} are writable. New work branches are
        selected and planned here. Protected writes are blocked in regulated
        mode; merge separately with a signature.
      </p>
      {op.refError ? (
        <p role="alert" className="m-0 text-sm text-bad-text">
          {op.refError}
        </p>
      ) : null}
    </div>
  )
}

function EstimatePanel({ operation: op }: { operation: Materialization }) {
  const pending =
    op.estimate.kind === 'pending' && op.estimate.intent === op.intent
  return (
    <>
      {op.ready ? (
        <div
          className="grid grid-cols-2 gap-2.5"
          aria-label="Verified materialization plan"
        >
          <Stat
            label="Planned runs"
            value={op.ready.partition_count}
            className="[&>span:first-of-type]:text-[15px]"
          />
          <Stat
            label="Selected assets"
            value={op.ready.selected_assets.length}
            className="[&>span:first-of-type]:text-[15px]"
          />
        </div>
      ) : null}
      <Button
        type="button"
        variant="outline"
        disabled={op.locked || !op.inputValid || !op.target || pending}
        onClick={() => void op.refreshEstimate()}
      >
        {pending ? 'Planning…' : 'Review plan'}
      </Button>
      {op.estimate.kind === 'failed' && op.estimate.intent === op.intent ? (
        <p role="alert" className="m-0 text-sm text-bad-text">
          {op.estimate.message}
        </p>
      ) : null}
      {op.ready ? (
        <div
          role="status"
          className="flex flex-col gap-1.5 break-words text-[12.5px] text-muted-foreground"
        >
          <span>
            Write ref{' '}
            <Mono>
              {op.ready.nessie_ref}@{op.ready.ref_hash?.slice(0, 12)}
            </Mono>
          </span>
          <span>
            {op.ready.job_selection === 'automatic'
              ? 'Automatic asset run'
              : op.ready.job_name}{' '}
            · execution revision{' '}
            <Mono>{op.ready.job_snapshot_id?.slice(0, 12)}</Mono>
          </span>
          <span>Assets: {op.ready.selected_assets.join(', ')}</span>
          <span>
            {op.ready.partition_keys.length
              ? `Partitions: ${op.ready.partition_keys.join(', ')}`
              : 'No partition key. One configured run.'}
          </span>
        </div>
      ) : null}
      <p className="m-0 text-[12.5px] text-muted-foreground">
        The plan verifies run count, asset selection and write ref. Future rows,
        bytes, duration and cost are not estimated.
      </p>
    </>
  )
}

function OperationResult({
  operation: op,
  env,
}: {
  operation: Materialization
  env: Env
}) {
  if (op.state.kind === 'failed')
    return (
      <div role="alert" className="text-sm text-bad-text">
        {op.state.message} Retry reuses the same operation key. Inspect run
        history if the response was lost.{' '}
        <Button type="button" variant="outline" onClick={() => op.reset(false)}>
          Edit and re-estimate
        </Button>
      </div>
    )
  if (op.state.kind !== 'accepted') return null
  const result = op.state.response.result
  return (
    <div role="status" className="flex flex-col gap-2 text-sm">
      {result.accepted
        ? 'Dagster accepted the planned runs.'
        : 'Dagster did not accept every planned run. Check the launched runs before starting another operation.'}{' '}
      Acceptance is not completion.
      {result.run_ids.map((run) => (
        <Link
          key={run}
          to="/pipelines/$jobName"
          params={{ jobName: result.job_name }}
          search={{ env, run }}
        >
          View run {run}
        </Link>
      ))}
      {result.runs
        .filter((run) => !run.accepted)
        .map((run, index) => (
          <p key={index} role="alert">
            {run.message || 'Run was not accepted.'}
          </p>
        ))}
      <Button type="button" variant="outline" onClick={() => op.reset(true)}>
        New materialization
      </Button>
    </div>
  )
}

function OperationFooter({
  operation: op,
  onClose,
}: {
  operation: Materialization
  onClose: () => void
}) {
  return (
    <DialogFooter className="flex-wrap">
      <span className="w-full text-[13px] text-muted-foreground sm:w-auto">
        Runs in Dagster with a pinned plan
      </span>
      <Button
        type="button"
        variant="outline"
        disabled={op.state.kind === 'pending' || op.branchBusy}
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
          !op.canSubmit ||
          op.state.kind === 'pending' ||
          op.state.kind === 'accepted'
        }
      >
        {op.state.kind === 'pending'
          ? 'Submitting…'
          : op.state.kind === 'failed'
            ? 'Retry request'
            : op.mode === 'latest'
              ? 'Run now'
              : op.mode === 'full'
                ? 'Start full refresh'
                : 'Start backfill'}
      </Button>
    </DialogFooter>
  )
}

export function MaterializeDialog(props: MaterializeDialogProps) {
  const { open, onClose, assetId, env, jobs, onExplicitPartitions } = props
  const op = useMaterialization(props)
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && op.state.kind !== 'pending' && !op.branchBusy) onClose()
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => void op.submit(event)}
          className="flex min-h-0 flex-col"
        >
          <DialogHeader>
            <DialogTitle>Materialize</DialogTitle>
            <DialogDescription>
              <Mono className="text-foreground">{assetId}</Mono> · via{' '}
              <Mono>{op.job || 'Automatic asset run'}</Mono> · {env}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            <LoadingFields
              operation={op}
              jobs={jobs}
              onExplicitPartitions={onExplicitPartitions}
            />
            <WriteTargetField operation={op} env={env} />
            <CheckLine
              checked={op.rebuild}
              disabled={op.locked}
              onCheckedChange={op.setRebuild}
            >
              Also rebuild downstream assets in the same execution
            </CheckLine>
            <EstimatePanel operation={op} />
            {op.mode === 'full' ? (
              <Field>
                <FieldLabel htmlFor="mz-confirm">
                  Type {assetId} to confirm full refresh
                </FieldLabel>
                <Input
                  id="mz-confirm"
                  disabled={op.locked}
                  value={op.destructive}
                  onChange={(event) => op.setDestructive(event.target.value)}
                />
                <FieldDescription>
                  Full refresh replaces data. The API requires a verified human
                  MFA session from the last five minutes.
                </FieldDescription>
              </Field>
            ) : null}
            <CheckLine
              checked={op.confirmed}
              disabled={op.locked || !op.ready}
              onCheckedChange={op.setConfirmed}
            >
              I confirm this operation in {env} on{' '}
              {op.target || 'the selected branch'}.
            </CheckLine>
            <OperationResult operation={op} env={env} />
          </DialogBody>
          <OperationFooter operation={op} onClose={onClose} />
        </form>
      </DialogContent>
      <NewBranchDialog
        key={`${env}:${op.branchOpen}`}
        open={op.branchOpen}
        env={env}
        refs={op.refs}
        busy={op.branchBusy}
        error={op.branchError}
        onClose={() => {
          if (!op.branchBusy) op.setBranchOpen(false)
        }}
        onCreate={(name, fromRef) => void op.addBranch(name, fromRef)}
      />
    </Dialog>
  )
}
