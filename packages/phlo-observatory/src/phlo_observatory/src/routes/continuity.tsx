/**
 * /continuity route. Capability-gated continuity actions in Observatory
 * The supported-operation surface comes only from the backend inventory
 * (GET /api/continuity/operations), every destructive
 * action is plan-first with an exact, immutable plan-token confirmation,
 * apply is once per intent under a durable idempotency key, and every
 * outcome is verified against the canonical journal lookup rendered with
 * the proven, pending-incomplete, and failed verification states.
 *
 * Unsupported or weakly bound operations (orphan_delete, anything the
 * inventory does not list) never render an actionable control, and nothing
 * here adds backend behavior or broadens a provider boundary.
 */
import { createFileRoute } from '@tanstack/react-router'
import { ShieldAlert } from 'lucide-react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import type {
  ContinuityApplyResult,
  ContinuityOperationsInventory,
  ContinuityPlan,
} from '@/observatory/api/continuity'
import type { ContinuityActionVerification } from '@/observatory/api/continuityVerification'
import type { ObservatoryResourceResult } from '@/observatory/api/types'
import {
  applyContinuityOperation,
  getContinuityOperations,
  getContinuityVerification,
  newContinuityIdempotencyKey,
  parseContinuityApiError,
  planContinuityOperation,
} from '@/observatory/api/continuity'
import { startContinuityVerification } from '@/observatory/api/continuityVerification'
import { loadCachedResource } from '@/observatory/routes/liveResource'
import { Page, PageHeader } from '@/components/observatory/page'
import {
  InspectorSection,
  SplitView,
} from '@/components/observatory/split-view'
import { Fact, FactGrid } from '@/components/observatory/key-value'
import { EmptyBlock, LoadingBlock } from '@/components/observatory/states'
import { SectionCard } from '@/components/observatory/section'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/continuity')({
  component: Continuity,
})

type ContinuityAction = 'backup' | 'restore' | 'upgrade' | 'maintenance'

const ACTION_LABELS: Record<ContinuityAction, string> = {
  backup: 'Backup create',
  restore: 'Restore to explicit target',
  upgrade: 'Version upgrade',
  maintenance: 'Maintenance',
}

/** Verification card tone for each verification state. */
const VERIFICATION_TONES: Record<
  ContinuityActionVerification['state'],
  'ok' | 'warning' | 'error'
> = {
  proven: 'ok',
  'pending-incomplete': 'warning',
  failed: 'error',
}

const TONE_CLASS: Record<'ok' | 'warning' | 'error', string> = {
  ok: 'border-status-ok/40 bg-status-ok/5',
  warning: 'border-status-warning/40 bg-status-warning/5',
  error: 'border-status-error/40 bg-status-error/5',
}

/**
 * Outcome codes that close the apply intent permanently: the durable journal
 * now owns the operation, so the UI must never offer a resubmission with a
 * new key (the backend blocks it; the UI must not suggest it).
 */
const INTENT_CLOSING_ERROR_CODES = new Set([
  'apply_outcome_unknown',
  'unknown_outcome_blocks_replay',
  'conflicting_claim',
  'mutation_succeeded_audit_failed',
])

export function Continuity() {
  const [inventory, setInventory] =
    useState<ObservatoryResourceResult<ContinuityOperationsInventory> | null>(
      null,
    )
  const [action, setAction] = useState<ContinuityAction | null>(null)

  useEffect(() => {
    let cancelled = false
    void loadCachedResource(
      'observatory:continuity-operations',
      () => getContinuityOperations(),
      { force: true, staleMs: 60_000 },
    ).then((next) => {
      if (!cancelled) setInventory(next)
    })
    return () => {
      cancelled = true
    }
  }, [])

  const operations = inventory?.data?.operations ?? []
  const unsupported = inventory?.data?.unsupported ?? []
  // A family gets a control only when the backend inventory lists it.
  const availableActions = useMemo(() => {
    const listed = new Set(operations.map((entry) => entry.family))
    return (Object.keys(ACTION_LABELS) as Array<ContinuityAction>).filter(
      (candidate) => listed.has(candidate),
    )
  }, [operations])

  return (
    <Page>
      <PageHeader
        actions={
          <Badge variant="secondary">
            {inventory === null
              ? 'Loading'
              : `${operations.length} supported operations`}
          </Badge>
        }
        description="Supported continuity capabilities: verified backups, explicit-target restores, bounded maintenance, and the supported version upgrade — plan-first, confirmed, applied once, and evidence-verified."
        title="Continuity"
      />
      {inventory === null ? (
        <SectionCard>
          <LoadingBlock
            className="p-3"
            label="Reading continuity capabilities"
          />
        </SectionCard>
      ) : inventory.error ? (
        <CapabilityMissingPanel detail={inventory.error} />
      ) : (
        <>
          <SplitView
            inspector={
              <InspectorSection label="Explicitly unsupported">
                {unsupported.length > 0 ? (
                  <div className="divide-border divide-y border-y">
                    {unsupported.map((entry) => (
                      <div
                        className="flex items-start gap-2 py-2"
                        key={entry.operation}
                      >
                        <ShieldAlert className="text-muted-foreground mt-0.5 size-3.5 flex-none" />
                        <span className="min-w-0">
                          <span className="text-foreground block font-mono text-[11px]">
                            {entry.operation}
                          </span>
                          <span className="text-muted-foreground block text-[10px]/relaxed">
                            {entry.reason} Never offered as an action.
                          </span>
                        </span>
                      </div>
                    ))}
                  </div>
                ) : (
                  <p className="text-muted-foreground text-xs">
                    No unsupported operations were reported.
                  </p>
                )}
              </InspectorSection>
            }
            inspectorWidth="w-[22rem]"
            list={
              <SectionCard
                className="ring-0"
                description="These are the only continuity actions Observatory offers, one per backend family. Every action runs explain, confirm, act, verify: a dry-run plan first, an exact confirmation of the reviewed plan token, one idempotent apply, and canonical durable evidence before any success is claimed."
                title="Supported operations"
              >
                <div className="divide-border divide-y">
                  {operations.map((entry) => (
                    <div
                      className="flex items-center justify-between gap-2 px-3 py-2"
                      key={entry.operation}
                    >
                      <span className="text-foreground font-mono text-[11px]">
                        {entry.operation}
                      </span>
                      <span className="text-muted-foreground font-mono text-[10px]">
                        {[
                          `${entry.family} family`,
                          `${entry.surface} surface`,
                          entry.requires_confirmation
                            ? 'plan-token confirmation required'
                            : 'no plan confirmation',
                        ].join(' · ')}
                      </span>
                    </div>
                  ))}
                </div>
                {availableActions.length > 0 ? (
                  <div className="flex flex-wrap items-center gap-2 border-t px-3 py-2.5">
                    {availableActions.map((candidate) => (
                      <Button
                        key={candidate}
                        onClick={() => setAction(candidate)}
                        size="sm"
                        type="button"
                        variant={action === candidate ? 'default' : 'outline'}
                      >
                        {ACTION_LABELS[candidate]}
                      </Button>
                    ))}
                  </div>
                ) : null}
              </SectionCard>
            }
          />
          {action && <ContinuityActionPanel action={action} />}
        </>
      )}
    </Page>
  )
}

function CapabilityMissingPanel({ detail }: { detail: string }) {
  return (
    <SectionCard>
      <EmptyBlock
        description="The guarded continuity action API is not reachable, so no continuity control is offered. Observatory never approximates these operations client-side."
        title="Continuity is not available in this stack"
      />
      <FactGrid className="mx-auto max-w-md pb-4">
        <Fact label="Status" value="not connected" />
        <Fact
          label="Next step"
          value="enable the phlo-api continuity surface"
        />
      </FactGrid>
      <p className="text-muted-foreground px-6 pb-5 text-center font-mono text-[10px] break-all">
        {detail}
      </p>
    </SectionCard>
  )
}

interface ContinuityFormState {
  backupTarget: string
  restoreBackupSet: string
  restoreTarget: string
  upgradeBackupSet: string
  upgradeTarget: string
  upgradeFromVersion: string
  upgradeToVersion: string
  maintenanceOperation: string
  maintenanceTable: string
  maintenanceRef: string
}

const EMPTY_FORM: ContinuityFormState = {
  backupTarget: '',
  restoreBackupSet: '',
  restoreTarget: '',
  upgradeBackupSet: '',
  upgradeTarget: '',
  upgradeFromVersion: '0.14.0',
  upgradeToVersion: '0.15.0',
  maintenanceOperation: 'compact',
  maintenanceTable: '',
  maintenanceRef: 'main',
}

/**
 * One panel per continuity action: plan-first explain (except apply-only
 * backup create), exact confirmation of the reviewed plan token, apply-once
 * under one durable idempotency key, and bounded canonical verification.
 */
function ContinuityActionPanel({ action }: { action: ContinuityAction }) {
  const [form, setForm] = useState<ContinuityFormState>(EMPTY_FORM)
  const [plan, setPlan] = useState<ContinuityPlan | null>(null)
  const [planning, setPlanning] = useState(false)
  const [planError, setPlanError] = useState<string | null>(null)
  const [outcome, setOutcome] = useState<ApplyOutcome | null>(null)
  const [verification, setVerification] =
    useState<ContinuityActionVerification | null>(null)
  const cancelVerificationRef = useRef<(() => void) | null>(null)

  useEffect(
    () => () => {
      cancelVerificationRef.current?.()
    },
    [],
  )

  const startVerification = useCallback((operationId: string) => {
    cancelVerificationRef.current?.()
    setVerification(null)
    cancelVerificationRef.current = startContinuityVerification({
      operationId,
      lookup: async (handle) => {
        const result = await getContinuityVerification({
          data: { operationId: handle },
        })
        return result.data
      },
      onState: setVerification,
    })
  }, [])

  const stopVerification = useCallback(() => {
    cancelVerificationRef.current?.()
    cancelVerificationRef.current = null
  }, [])

  const setField = (field: keyof ContinuityFormState) => (value: string) =>
    setForm((current) => ({ ...current, [field]: value }))

  const submitPlan = () => {
    if (planning) return
    setPlanning(true)
    setPlanError(null)
    const request = buildPlanRequest(action, form)
    if (!request) {
      setPlanning(false)
      setPlanError('Fill every bound field before planning.')
      return
    }
    void planContinuityOperation({ data: request })
      .then((next) => {
        if (next.error) {
          const parsed = parseContinuityApiError(new Error(next.error))
          setPlanError(
            parsed
              ? `Backend guard: ${parsed.code}. The plan was not issued.`
              : next.error,
          )
          return
        }
        if (next.data) {
          setPlan(next.data)
          setOutcome(null)
          setVerification(null)
        }
      })
      .finally(() => setPlanning(false))
  }

  const discardPlan = () => {
    cancelVerificationRef.current?.()
    cancelVerificationRef.current = null
    setPlan(null)
    setOutcome(null)
    setVerification(null)
  }

  const onApplied = useCallback(
    (result: ContinuityApplyResult, idempotencyKey: string) => {
      setOutcome({ result, idempotencyKey, closed: false })
      startVerification(result.operation_id)
    },
    [startVerification],
  )

  const onClosedIntent = useCallback(() => {
    setOutcome((current) => (current ? { ...current, closed: true } : current))
  }, [])

  return (
    <SectionCard
      actions={
        plan && (
          <Button onClick={discardPlan} size="xs" type="button" variant="ghost">
            Discard plan
          </Button>
        )
      }
      title={ACTION_LABELS[action]}
    >
      <div className="flex flex-col gap-3 p-3">
        {action === 'backup' ? (
          <div className="flex flex-col gap-2">
            <p className="text-muted-foreground text-xs/relaxed">
              Backup create runs directly: one immutable, verified backup set
              written to the exact reviewed target directory, journaled and
              idempotent.
            </p>
            <TextField
              label="Backup target directory"
              onChange={setField('backupTarget')}
              value={form.backupTarget}
            />
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <PlanInputFields action={action} setField={setField} value={form} />
            <div>
              <Button
                disabled={planning}
                onClick={submitPlan}
                size="sm"
                type="button"
                variant="outline"
              >
                {planning ? 'Planning…' : 'Create dry-run plan'}
              </Button>
            </div>
            <p className="text-muted-foreground text-[11px]/relaxed">
              Planning is mutation-free: the backend returns an immutable plan
              bound to the exact set digest, target, and bounds. Apply stays
              disabled until the plan is reviewed and confirmed exactly.
            </p>
          </div>
        )}
        {planError && (
          <div className="border-status-error/40 bg-status-error/5 flex flex-col gap-1 border px-3 py-2">
            <strong className="text-status-error text-xs">
              Plan not issued
            </strong>
            <span className="text-muted-foreground font-mono text-[10px] break-all">
              {planError}
            </span>
          </div>
        )}

        {(action === 'backup' || plan !== null) && (
          <ConfirmAndApply
            action={action}
            expectedConfirmation={
              action === 'backup'
                ? form.backupTarget.trim()
                : (plan?.planToken ?? '')
            }
            onApplied={onApplied}
            onClosedIntent={onClosedIntent}
            plan={plan}
          />
        )}

        {outcome && (
          <ApplyOutcomeCards
            onCloseVerification={stopVerification}
            outcome={outcome}
            verification={verification}
          />
        )}
      </div>
    </SectionCard>
  )
}

interface ApplyOutcome {
  result: ContinuityApplyResult
  idempotencyKey: string
  /** True when the intent is closed: no resubmission may be offered. */
  closed: boolean
}

/**
 * Exact confirmation + apply-once. The apply is enabled only when the typed
 * confirmation equals the reviewed plan token (or, for apply-only backup
 * create, the exact reviewed target). One idempotency key is generated per
 * presented intent and reused for any transport-level resubmission; a guarded
 * result or an intent-closing journal outcome retires the key forever.
 */
function ConfirmAndApply({
  action,
  expectedConfirmation,
  plan,
  onApplied,
  onClosedIntent,
}: {
  action: ContinuityAction
  expectedConfirmation: string
  plan: ContinuityPlan | null
  onApplied: (result: ContinuityApplyResult, idempotencyKey: string) => void
  onClosedIntent: () => void
}) {
  const [idempotencyKey] = useState(() => newContinuityIdempotencyKey())
  const [confirmation, setConfirmation] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [applyError, setApplyError] = useState<string | null>(null)
  const [errorCode, setErrorCode] = useState<string | null>(null)
  const [submitted, setSubmitted] = useState(false)

  const confirmed =
    expectedConfirmation !== '' && confirmation.trim() === expectedConfirmation

  const apply = () => {
    if (submitting || submitted || !confirmed) return
    setSubmitting(true)
    setApplyError(null)
    setErrorCode(null)
    const request = buildApplyRequest(
      action,
      plan,
      idempotencyKey,
      confirmation.trim(),
    )
    if (!request) {
      setSubmitting(false)
      setApplyError(
        'The reviewed intent is incomplete; re-plan before applying.',
      )
      return
    }
    void applyContinuityOperation({ data: request })
      .then((next) => {
        if (next.error) {
          const parsed = parseContinuityApiError(new Error(next.error))
          setErrorCode(parsed?.code ?? null)
          setApplyError(next.error)
          if (parsed && INTENT_CLOSING_ERROR_CODES.has(parsed.code)) {
            // Unknown outcome / journal conflict: the durable journal owns
            // this operation now. Close the intent; offer verification only.
            setSubmitted(true)
            onClosedIntent()
          }
          return
        }
        if (next.data) {
          setSubmitted(true)
          onApplied(next.data, idempotencyKey)
        }
      })
      .finally(() => setSubmitting(false))
  }

  return (
    <div className="border-border flex flex-col gap-3 border-t pt-3">
      {plan && <PlanFacts plan={plan} />}
      {!submitted && (
        <label className="flex flex-col gap-1">
          <span className="text-muted-foreground text-[11px] font-medium">
            {action === 'backup'
              ? 'Confirmation: retype the exact backup target'
              : 'Confirmation: retype the exact plan token'}
          </span>
          <Input
            autoComplete="off"
            onChange={(event) => setConfirmation(event.target.value)}
            placeholder={
              action === 'backup' ? 'reviewed target' : expectedConfirmation
            }
            spellCheck={false}
            value={confirmation}
          />
        </label>
      )}
      <div className="flex items-center justify-between gap-2">
        <span className="text-foreground text-[11px]">Idempotency key</span>
        <span className="text-muted-foreground font-mono text-[10px]">
          {idempotencyKey}
        </span>
      </div>
      <div>
        <Button
          disabled={submitting || submitted || !confirmed}
          onClick={apply}
          size="sm"
          type="button"
        >
          {submitting
            ? 'Applying…'
            : submitted
              ? 'Applied'
              : `Apply ${ACTION_LABELS[action].toLowerCase()}`}
        </Button>
      </div>
      {applyError && (
        <div className="border-status-error/40 bg-status-error/5 flex flex-col gap-1 border px-3 py-2">
          <strong className="text-status-error text-xs">
            Apply could not be completed
          </strong>
          <span className="text-muted-foreground text-[11px]/relaxed">
            {errorCode ? `Backend guard: ${errorCode}.` : ''}{' '}
            {submitted
              ? 'This intent is closed: the durable journal owns the operation, so no new idempotency key may replay it. Resolve the outcome through the canonical verification handle.'
              : 'Resubmitting reuses the same idempotency key and plan token, so the durable claim store replays instead of re-invoking the provider.'}
          </span>
          <span className="text-muted-foreground font-mono text-[10px] break-all">
            {applyError}
          </span>
        </div>
      )}
    </div>
  )
}

function PlanFacts({ plan }: { plan: ContinuityPlan }) {
  const facts: Array<[string, string]> = [
    ['Plan token', plan.planToken],
    ['Operation handle', plan.operationId],
  ]
  if (plan.kind === 'restore') {
    facts.push(
      ['Backup set', plan.plan.backup_set_id],
      ['Set digest', plan.plan.set_digest],
      ['Bound target', plan.plan.target.target_id],
      ['Provider order', plan.plan.provider_order.join(', ')],
      ['Valid until', plan.plan.expires_at],
    )
  }
  if (plan.kind === 'upgrade') {
    facts.push(
      ['Version pair', `${plan.plan.from_version} → ${plan.plan.to_version}`],
      ['Backup set', plan.plan.backup_set_id],
      ['Backup digest', plan.plan.backup_digest],
      ['Migration digest', plan.plan.migration_digest],
      ['Bound target', plan.plan.target.target_id],
      ['Valid until', plan.plan.expires_at],
    )
  }
  if (plan.kind === 'maintenance') {
    facts.push(
      ['Maintenance operation', plan.plan.operation],
      ['Table', plan.plan.table_name],
      ['Ref', plan.plan.ref],
    )
  }
  return (
    <FactGrid>
      {facts.map(([label, value]) => (
        <Fact key={label} label={label} value={value} />
      ))}
    </FactGrid>
  )
}

function ApplyOutcomeCards({
  outcome,
  verification,
  onCloseVerification,
}: {
  outcome: ApplyOutcome
  verification: ContinuityActionVerification | null
  onCloseVerification: () => void
}) {
  const result = outcome.result
  const accepted = result.accepted === true || result.status === 'completed'
  return (
    <div className="flex flex-col gap-2">
      <div
        className={cn(
          'flex flex-col gap-1 border px-3 py-2',
          TONE_CLASS[accepted ? 'ok' : 'warning'],
        )}
      >
        <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
          Outcome
        </span>
        <strong className="text-foreground text-xs">
          {accepted
            ? 'Apply accepted by the guarded endpoint.'
            : 'Apply result recorded.'}
        </strong>
        <span className="text-muted-foreground font-mono text-[10px] break-all">
          Operation handle {result.operation_id}. Idempotency key{' '}
          {outcome.idempotencyKey} — replaying this intent answers from the
          durable journal; it never re-invokes the provider.
        </span>
        {typeof result.state === 'string' && (
          <span className="text-muted-foreground font-mono text-[10px]">
            Reported state: {result.state}.
          </span>
        )}
      </div>
      <div
        className={cn(
          'flex flex-col gap-1 border px-3 py-2',
          TONE_CLASS[
            verification ? VERIFICATION_TONES[verification.state] : 'warning'
          ],
        )}
      >
        <span className="text-muted-foreground text-[10px] font-medium tracking-widest uppercase">
          Verification
        </span>
        {verification ? (
          <>
            <strong className="text-foreground text-xs">
              {verification.headline}
            </strong>
            <span className="text-muted-foreground font-mono text-[10px] break-all">
              {verification.detail}
            </span>
            {verification.replayBlocked && (
              <span className="text-muted-foreground font-mono text-[10px]">
                Replay blocked: no new idempotency key can resubmit this
                operation.
              </span>
            )}
            {!verification.replayBlocked && (
              <div>
                <Button
                  onClick={onCloseVerification}
                  size="xs"
                  type="button"
                  variant="outline"
                >
                  Stop verifying
                </Button>
              </div>
            )}
          </>
        ) : (
          <strong className="text-foreground text-xs">
            Checking canonical durable evidence…
          </strong>
        )}
      </div>
    </div>
  )
}

function PlanInputFields({
  action,
  setField,
  value,
}: {
  action: ContinuityAction
  setField: (field: keyof ContinuityFormState) => (value: string) => void
  value: ContinuityFormState
}) {
  if (action === 'restore') {
    return (
      <>
        <TextField
          label="Backup set directory"
          onChange={setField('restoreBackupSet')}
          value={value.restoreBackupSet}
        />
        <TextField
          label="Explicit restore target"
          onChange={setField('restoreTarget')}
          value={value.restoreTarget}
        />
      </>
    )
  }
  if (action === 'upgrade') {
    return (
      <>
        <TextField
          label="Backup set directory (verified pre-upgrade backup)"
          onChange={setField('upgradeBackupSet')}
          value={value.upgradeBackupSet}
        />
        <TextField
          label="Upgrade target"
          onChange={setField('upgradeTarget')}
          value={value.upgradeTarget}
        />
        <TextField
          label="From version (supported pair only)"
          onChange={setField('upgradeFromVersion')}
          value={value.upgradeFromVersion}
        />
        <TextField
          label="To version"
          onChange={setField('upgradeToVersion')}
          value={value.upgradeToVersion}
        />
      </>
    )
  }
  return (
    <>
      <label className="flex flex-col gap-1">
        <span className="text-muted-foreground text-[11px] font-medium">
          Maintenance operation
        </span>
        <select
          className="border-input bg-background h-8 border px-2 text-xs"
          onChange={(event) =>
            setField('maintenanceOperation')(event.target.value)
          }
          value={value.maintenanceOperation}
        >
          <option value="compact">compact</option>
          <option value="snapshot_expiry">snapshot_expiry</option>
        </select>
      </label>
      <TextField
        label="Table"
        onChange={setField('maintenanceTable')}
        value={value.maintenanceTable}
      />
      <TextField
        label="Ref"
        onChange={setField('maintenanceRef')}
        value={value.maintenanceRef}
      />
    </>
  )
}

function buildPlanRequest(
  action: ContinuityAction,
  form: ContinuityFormState,
): Parameters<typeof planContinuityOperation>[0]['data'] | null {
  if (action === 'backup') return null // backup.create is apply-only
  if (action === 'restore') {
    if (!form.restoreBackupSet.trim() || !form.restoreTarget.trim()) return null
    return {
      operation: 'restore.plan',
      backupSet: form.restoreBackupSet.trim(),
      target: form.restoreTarget.trim(),
    }
  }
  if (action === 'upgrade') {
    if (
      !form.upgradeBackupSet.trim() ||
      !form.upgradeTarget.trim() ||
      !form.upgradeFromVersion.trim() ||
      !form.upgradeToVersion.trim()
    ) {
      return null
    }
    return {
      operation: 'upgrade.plan',
      backupSet: form.upgradeBackupSet.trim(),
      target: form.upgradeTarget.trim(),
      fromVersion: form.upgradeFromVersion.trim(),
      toVersion: form.upgradeToVersion.trim(),
    }
  }
  if (!form.maintenanceTable.trim()) return null
  return {
    operation: 'maintenance.plan',
    maintenanceOperation: form.maintenanceOperation,
    table: form.maintenanceTable.trim(),
    ref: form.maintenanceRef.trim() || 'main',
  }
}

function buildApplyRequest(
  action: ContinuityAction,
  plan: ContinuityPlan | null,
  idempotencyKey: string,
  confirmationToken: string,
): Parameters<typeof applyContinuityOperation>[0]['data'] | null {
  if (action === 'backup') {
    // backup.create is apply-only (no plan token); the confirmation is the
    // exact reviewed target typed by the operator.
    return {
      operation: 'backup.create',
      idempotencyKey,
      target: confirmationToken,
    }
  }
  if (action === 'restore' && plan?.kind === 'restore') {
    return {
      operation: 'restore.apply',
      idempotencyKey,
      plan: plan.plan,
      confirmationToken,
    }
  }
  if (action === 'upgrade' && plan?.kind === 'upgrade') {
    return {
      operation: 'upgrade.apply',
      idempotencyKey,
      plan: plan.plan,
      confirmationToken,
    }
  }
  if (action === 'maintenance' && plan?.kind === 'maintenance') {
    return {
      operation: 'maintenance.apply',
      idempotencyKey,
      plan: plan.plan,
      confirmationToken,
      table: plan.plan.table_name,
      ref: plan.plan.ref,
    }
  }
  return null
}

function TextField({
  label,
  onChange,
  value,
}: {
  label: string
  onChange: (value: string) => void
  value: string
}) {
  return (
    <label className="flex flex-col gap-1">
      <span className="text-muted-foreground text-[11px] font-medium">
        {label}
      </span>
      <Input
        autoComplete="off"
        onChange={(event) => onChange(event.target.value)}
        spellCheck={false}
        value={value}
      />
    </label>
  )
}
