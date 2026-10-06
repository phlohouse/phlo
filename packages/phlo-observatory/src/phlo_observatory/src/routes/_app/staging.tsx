/** Defines staging comparison, resynchronisation, checking, and promotion workflows. */
import * as React from 'react'
import { Link, createFileRoute, useRouter } from '@tanstack/react-router'
import { ArrowRightIcon, Loader2Icon, RefreshCwIcon } from 'lucide-react'
import type { StagingCandidate, StagingChecks } from '@/lib/data/api/staging'
import {
  getStagingOverview,
  promoteStaging,
  resyncStaging,
  runStagingChecks,
  stagingChecksPassed,
} from '@/lib/data/api/staging'
import { Eyebrow, PageBody, PageHeader } from '@/components/phlo/page'
import { KpiCard } from '@/components/phlo/kpi'
import { EmptyState } from '@/components/phlo/states'
import { Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import { Card } from '@/components/ui/card'
import { CheckLine } from '@/components/ui/checkbox'
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Field, FieldLabel } from '@/components/ui/field'
import { Textarea } from '@/components/ui/input'

export const Route = createFileRoute('/_app/staging')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: ({ deps }) => getStagingOverview({ data: { env: deps.env } }),
  head: () => ({ meta: [{ title: 'Staging · phlo' }] }),
  component: StagingPage,
})

function operationKey(action: string, intent: string) {
  const storageKey = `phlo:staging:${action}:${intent}`
  const existing = sessionStorage.getItem(storageKey)
  if (existing) return existing
  const value = crypto.randomUUID()
  sessionStorage.setItem(storageKey, value)
  return value
}

function StagingPage() {
  const data = Route.useLoaderData()
  if (data.kind === 'prod')
    return (
      <>
        <PageHeader title="Staging" />
        <PageBody>
          <EmptyState
            title="Staging controls are not available from prod"
            action={
              <Link
                to="/staging"
                search={{ env: 'staging' }}
                className={buttonVariants()}
              >
                Switch to staging
              </Link>
            }
          >
            Switch environment to inspect or operate on the staging candidate.
            No staging request was made.
          </EmptyState>
        </PageBody>
      </>
    )
  return (
    <StagingOverview candidate={data.candidate} history={data.history.items} />
  )
}

function StagingOverview({
  candidate,
  history,
}: {
  candidate: StagingCandidate
  history: Array<{
    timestamp: string
    operation: string
    target: string
    subject: string
    result: { status?: string }
  }>
}) {
  const router = useRouter()
  const [checks, setChecks] = React.useState<StagingChecks>()
  const [busy, setBusy] = React.useState<'checks' | 'promote' | 'resync'>()
  const [error, setError] = React.useState<string>()
  const [dialog, setDialog] = React.useState<'promote' | 'resync'>()
  const candidateId = candidate.candidate_id
  React.useEffect(() => {
    setChecks(undefined)
    setError(undefined)
    setDialog(undefined)
  }, [candidateId])

  const run = async (
    kind: NonNullable<typeof busy>,
    work: () => Promise<unknown>,
  ) => {
    if (busy) return
    setBusy(kind)
    setError(undefined)
    try {
      await work()
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : 'The staging operation failed.',
      )
    } finally {
      setBusy(undefined)
    }
  }
  const refresh = async () => {
    setChecks(undefined)
    setDialog(undefined)
    await router.invalidate()
  }
  const checkCandidate = async () => {
    setChecks(undefined)
    const result = await runStagingChecks({
      data: {
        candidateId,
        idempotencyKey: operationKey('checks', candidateId),
      },
    })
    sessionStorage.removeItem(`phlo:staging:checks:${candidateId}`)
    setChecks(result)
  }
  const codeChanges = candidate.code_changes.map((entry, index) => {
    const separator = entry.indexOf('\t')
    return {
      status: separator < 0 ? '?' : entry.slice(0, separator),
      path:
        separator < 0
          ? (candidate.code_paths[index] ?? entry)
          : entry.slice(separator + 1),
    }
  })
  const jobInventory = environmentInventory(
    candidate.jobs.prod,
    candidate.jobs.staging,
  )
  const copyInventory = environmentInventory(
    candidate.copy_inventory.prod,
    candidate.copy_inventory.staging,
  )
  const checksPassed = stagingChecksPassed(
    candidate.candidate_id,
    candidate.check_readiness,
    checks,
  )

  return (
    <>
      <PageHeader
        title={
          <span className="flex items-center gap-2.5">
            Overview <Badge variant="warn">staging</Badge>
          </span>
        }
        meta={`Observed ${formatTime(candidate.observed_at)} · changes here never reach prod until promoted`}
        actions={
          <>
            <Button
              variant="outline"
              disabled={Boolean(busy)}
              onClick={() => void refresh()}
            >
              <RefreshCwIcon /> Refresh evidence
            </Button>
            <Button
              variant="outline"
              disabled={Boolean(busy)}
              onClick={() => setDialog('resync')}
            >
              Re-sync data…
            </Button>
            <Button
              variant={checksPassed ? 'default' : 'outline'}
              disabled={
                Boolean(busy) || !checksPassed || !candidate.code_paths.length
              }
              onClick={() => setDialog('promote')}
            >
              Promote code…
            </Button>
          </>
        }
      />
      <PageBody className="gap-4">
        {error ? (
          <div
            role="alert"
            className="rounded-lg border border-bad-line bg-bad-wash p-3 text-sm text-bad-ink"
          >
            {error}{' '}
            <Button
              variant="outline"
              size="sm"
              className="ml-2"
              onClick={() => void refresh()}
            >
              Reload current evidence
            </Button>
          </div>
        ) : null}
        <div className="grid grid-cols-2 gap-3 lg:grid-cols-4 lg:gap-3.5">
          <KpiCard
            label="Differs from prod"
            value={candidate.code_changes.length}
            qualifier="code changes"
            footer={`${candidate.prod_ref} → ${candidate.staging_ref}`}
          />
          <KpiCard
            label="Candidate checks"
            value={
              checks
                ? checks.items.filter((item) => item.status === 'passed').length
                : candidate.check_readiness.filter(
                    (item) => item.status === 'ready',
                  ).length
            }
            qualifier={
              checks ? `of ${checks.items.length} passing` : 'of 3 passing'
            }
            footer="Tests, contracts and audits"
          />
          <KpiCard
            label="Dagster jobs"
            value={candidate.jobs.staging.length}
            qualifier="in staging"
            footer={`${candidate.jobs.prod.length} in prod`}
          />
          <KpiCard
            label="Data copy"
            value={candidate.copy_inventory.staging.length}
            qualifier="tables"
            footer="Latest observed inventory"
          />
        </div>
        <div className="grid shrink-0 grid-cols-1 gap-4 lg:grid-cols-[minmax(0,1.25fr)_minmax(0,1fr)]">
          <Card
            id="promote"
            className="scroll-mt-4 px-4 py-4 lg:col-start-2 lg:px-5"
          >
            <div className="flex flex-wrap items-center gap-2">
              <h2 className="m-0 text-[15px] font-medium">Promote to prod</h2>
              <span className="text-[13px] text-muted-foreground">
                Each promotion is checked, confirmed and signed
              </span>
              <Eyebrow className="ml-auto">Candidate</Eyebrow>
              <Mono className="max-w-full truncate text-xs">{candidateId}</Mono>
            </div>
            <dl className="mt-3 grid gap-3 text-sm">
              <Evidence label="Git">
                <Mono className="break-all text-xs">
                  {candidate.prod_git_revision}
                </Mono>
                <ArrowRightIcon className="inline size-3 mx-2" />
                <Mono className="break-all text-xs">
                  {candidate.staging_git_revision}
                </Mono>
              </Evidence>
              <Evidence label="Nessie">
                <Mono>{candidate.prod_ref}</Mono> @{' '}
                <Mono className="break-all text-xs">{candidate.prod_hash}</Mono>
                <br />
                <Mono>{candidate.staging_ref}</Mono> @{' '}
                <Mono className="break-all text-xs">
                  {candidate.staging_hash}
                </Mono>
              </Evidence>
              <Evidence label="Dagster locations">
                prod: <Mono>{candidate.dagster_location}</Mono>
                <br />
                staging: <Mono>{candidate.staging_location}</Mono>
              </Evidence>
              <Evidence label="Scope">
                Promotion advances Git/code/config and reloads prod Dagster.
                Nessie data is not promoted.
              </Evidence>
            </dl>
          </Card>

          <div className="contents">
            <Inventory
              className="lg:col-start-1 lg:row-start-1 lg:row-span-4"
              title={`Differences from prod (${codeChanges.length})`}
              items={codeChanges.map((item) => ({
                name: item.path,
                note: gitStatus(item.status),
              }))}
              empty="No code paths differ."
            />
            <Card className="p-4 lg:p-5">
              <div className="flex items-center gap-3">
                <div>
                  <h2 className="m-0 text-[15px] font-medium">
                    Candidate checks
                  </h2>
                  <p className="m-0 text-xs text-muted-foreground">
                    Tests, contracts and audits run against this exact
                    candidate.
                  </p>
                </div>
                <Button
                  className="ml-auto"
                  variant="outline"
                  disabled={
                    Boolean(busy) || !candidate.check_configuration_ready
                  }
                  onClick={() => void run('checks', checkCandidate)}
                >
                  {busy === 'checks' ? (
                    <Loader2Icon className="animate-spin" />
                  ) : null}
                  Run checks
                </Button>
              </div>
              {checks ? (
                <ul className="mt-3 list-none p-0">
                  {checks.items.map((item) => (
                    <li
                      key={item.name}
                      className="border-t border-line-soft py-2 text-sm"
                    >
                      <Badge
                        variant={
                          item.status === 'passed'
                            ? 'ok'
                            : item.status === 'failed'
                              ? 'bad'
                              : 'warn'
                        }
                      >
                        {item.status}
                      </Badge>{' '}
                      <span className="ml-2">{item.name}</span>
                      {item.run_id ? (
                        <Mono className="ml-2 text-xs">{item.run_id}</Mono>
                      ) : null}
                      {item.message ? (
                        <div className="text-xs text-muted-foreground">
                          {item.message}
                        </div>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <ul className="mt-3 list-none p-0">
                  {candidate.check_readiness.map((item) => (
                    <li
                      key={item.name}
                      className="border-t border-line-soft py-2 text-sm"
                    >
                      <Badge variant={checkStatusVariant(item.status)}>
                        {item.status.replaceAll('_', ' ')}
                      </Badge>{' '}
                      <span className="ml-2">{item.name}</span>
                      {item.job_name ? (
                        <Mono className="ml-2 text-xs">{item.job_name}</Mono>
                      ) : null}
                      {item.run_id ? (
                        <Mono className="ml-2 text-xs">{item.run_id}</Mono>
                      ) : null}
                      <div className="text-xs text-muted-foreground">
                        {item.message}
                      </div>
                    </li>
                  ))}
                </ul>
              )}
              {!candidate.check_configuration_ready ? (
                <p className="mb-0 mt-2 text-xs text-muted-foreground">
                  Add the three executable check jobs to the staging Dagster
                  location and set PHLO_PROMOTION_DAGSTER_CHECK_JOBS in
                  tests,contracts,audits order before running checks.
                </p>
              ) : null}
            </Card>
            <Inventory
              title={`Dagster job inventory (${candidate.jobs.prod.length} prod / ${candidate.jobs.staging.length} staging)`}
              items={jobInventory}
              empty="No jobs were returned."
            />
            <Inventory
              title={`Where staging data comes from (${candidate.copy_inventory.prod.length} prod / ${candidate.copy_inventory.staging.length} staging tables)`}
              items={copyInventory}
              empty="No data-copy inventory was returned."
            />
          </div>
        </div>

        <Card className="p-4 lg:p-5">
          <h2 className="m-0 text-[15px] font-medium">Recent activity</h2>
          {history.length ? (
            <div className="mt-2 overflow-x-auto">
              <table className="w-full text-left text-sm">
                <thead>
                  <tr className="text-muted-foreground">
                    <th className="py-2">When</th>
                    <th>Subject</th>
                    <th>Target</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {[...history].reverse().map((item, index) => (
                    <tr
                      key={`${item.timestamp}:${index}`}
                      className="border-t border-line-soft"
                    >
                      <td className="py-2 pr-4 whitespace-nowrap">
                        {formatTime(item.timestamp)}
                      </td>
                      <td className="pr-4">{item.subject}</td>
                      <td className="pr-4">
                        <Mono className="break-all text-xs">{item.target}</Mono>
                      </td>
                      <td>
                        {typeof item.result.status === 'string'
                          ? item.result.status
                          : 'recorded'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="mb-0 text-sm text-muted-foreground">
              No promotion operations are recorded.
            </p>
          )}
        </Card>
      </PageBody>
      <ConfirmDialog
        kind="resync"
        open={dialog === 'resync'}
        busy={busy === 'resync'}
        error={error}
        candidate={candidate}
        onClose={() => !busy && setDialog(undefined)}
        onSubmit={() =>
          run('resync', async () => {
            await resyncStaging({
              data: {
                expectedProdHash: candidate.prod_hash,
                expectedStagingHash: candidate.staging_hash,
                confirm: true,
                idempotencyKey: operationKey(
                  'resync',
                  `${candidate.prod_hash}<-${candidate.staging_hash}`,
                ),
              },
            })
            await refresh()
          })
        }
      />
      <ConfirmDialog
        kind="promote"
        open={dialog === 'promote'}
        busy={busy === 'promote'}
        error={error}
        candidate={candidate}
        onClose={() => !busy && setDialog(undefined)}
        onSubmit={(justification) =>
          run('promote', async () => {
            await promoteStaging({
              data: {
                candidateId,
                prodRef: candidate.prod_ref,
                stagingRef: candidate.staging_ref,
                justification,
                confirm: true,
                signatureKey: operationKey(
                  'signature',
                  `${candidateId}:${justification}`,
                ),
                idempotencyKey: operationKey(
                  'promote',
                  `${candidateId}:${justification}`,
                ),
              },
            })
            await refresh()
          })
        }
      />
    </>
  )
}

function ConfirmDialog({
  kind,
  open,
  busy,
  error,
  candidate,
  onClose,
  onSubmit,
}: {
  kind: 'promote' | 'resync'
  open: boolean
  busy: boolean
  error?: string
  candidate: StagingCandidate
  onClose: () => void
  onSubmit: (justification: string) => void
}) {
  const [confirmed, setConfirmed] = React.useState(false),
    [justification, setJustification] = React.useState('')
  const promote = kind === 'promote'
  return (
    <Dialog
      open={open}
      onOpenChange={(value) => {
        if (!value && !busy) onClose()
      }}
    >
      <DialogContent>
        <form
          onSubmit={(event) => {
            event.preventDefault()
            if (!busy && confirmed && (!promote || justification.trim()))
              onSubmit(justification.trim())
          }}
        >
          <DialogHeader>
            <DialogTitle>
              {promote
                ? 'Promote staging code to prod'
                : 'Destructively re-sync staging data'}
            </DialogTitle>
            <DialogDescription className="break-all">
              {promote
                ? `Advance ${candidate.prod_ref} to staging Git revision and reload prod Dagster. Nessie data stays unchanged.`
                : `Move ${candidate.staging_ref} from ${candidate.staging_hash} to prod hash ${candidate.prod_hash}. Staging-only data changes may be lost.`}
            </DialogDescription>
          </DialogHeader>
          <DialogBody>
            {promote ? (
              <Field>
                <FieldLabel>Justification</FieldLabel>
                <Textarea
                  required
                  maxLength={1000}
                  rows={3}
                  value={justification}
                  onChange={(event) => setJustification(event.target.value)}
                />
              </Field>
            ) : null}
            <CheckLine
              checked={confirmed}
              onCheckedChange={(value) => setConfirmed(value === true)}
            >
              {promote
                ? 'I confirm this signed promotion of the displayed candidate.'
                : 'I confirm this destructive Nessie staging re-sync.'}
            </CheckLine>
            {error ? (
              <p role="alert" className="m-0 text-sm text-bad-ink">
                {error}
              </p>
            ) : null}
          </DialogBody>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              disabled={busy}
              onClick={onClose}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant={promote ? 'default' : 'destructive'}
              disabled={
                busy || !confirmed || (promote && !justification.trim())
              }
            >
              {busy
                ? 'Working…'
                : promote
                  ? 'Sign and promote'
                  : 'Re-sync staging data'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function Evidence({
  label,
  children,
}: {
  label: string
  children: React.ReactNode
}) {
  return (
    <div>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="m-0 mt-1 break-all">{children}</dd>
    </div>
  )
}

function checkStatusVariant(status: string): 'ok' | 'bad' | 'warn' {
  if (status === 'ready') return 'ok'
  if (status === 'failed') return 'bad'
  return 'warn'
}

function Inventory({
  className,
  title,
  items,
  empty,
}: {
  className?: string
  title: string
  items: Array<{ name: string; note: string }>
  empty: string
}) {
  return (
    <Card className={`p-4 lg:p-5 ${className ?? ''}`}>
      <h2 className="m-0 text-[15px] font-medium">{title}</h2>
      {items.length ? (
        <div
          role="region"
          aria-label={title}
          tabIndex={0}
          className="mt-2 max-h-64 overflow-y-auto focus-visible:outline-2 focus-visible:outline-solid focus-visible:outline-ring"
        >
          <ul className="m-0 list-none p-0">
            {items.map((item) => (
              <li
                key={`${item.note}:${item.name}`}
                className="flex gap-2 border-t border-line-soft py-2 text-sm"
              >
                <Badge variant="outline">{item.note}</Badge>
                <Mono className="break-all text-xs">{item.name}</Mono>
              </li>
            ))}
          </ul>
        </div>
      ) : (
        <p className="mb-0 text-sm text-muted-foreground">{empty}</p>
      )}
    </Card>
  )
}
function environmentInventory(prod: Array<string>, staging: Array<string>) {
  return [
    ...prod.map((name) => ({ name, note: 'prod' })),
    ...staging.map((name) => ({ name, note: 'staging' })),
  ]
}
function gitStatus(status: string) {
  return status === 'A'
    ? 'added'
    : status === 'M'
      ? 'modified'
      : status === 'D'
        ? 'deleted'
        : `Git ${status}`
}
function formatTime(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.valueOf()) ? value : date.toLocaleString('en-GB')
}
