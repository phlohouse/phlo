/** Defines the form for viewing and updating Observatory settings. */
import * as React from 'react'
import { createFileRoute, useRouter } from '@tanstack/react-router'
import { Loader2Icon } from 'lucide-react'
import type { ObservatoryServiceList } from '@/lib/data/api/client'
import type { Settings } from '@/lib/data/api/settings'
import {
  environmentServices,
  serviceHealthLabel,
  serviceHealthTone,
} from '@/lib/data/api/client'
import { getShell } from '@/lib/data/api/core'
import {
  emptySettings,
  getSettings,
  saveSettings,
  settingsSchema,
} from '@/lib/data/api/settings'
import { PageHeader } from '@/components/phlo/page'
import { Dot, LayerSwatch, Mono } from '@/components/phlo/status'
import { SettingsFrame } from '@/components/settings/frame'
import { Button } from '@/components/ui/button'
import { CardDescription, CardTitle } from '@/components/ui/card'
import { CheckLine } from '@/components/ui/checkbox'
import { Field, FieldLabel } from '@/components/ui/field'
import { Input } from '@/components/ui/input'
import { cn } from '@/lib/utils'

export const Route = createFileRoute('/_app/settings/')({
  loaderDeps: ({ search }) => ({ env: search.env }),
  loader: async ({ deps }) => {
    const [health, configuration] = await Promise.all([
      getShell({ data: deps.env }),
      getSettings()
        .then((value) => ({ value, error: null }))
        .catch((error: unknown) => ({
          value: null,
          error:
            error instanceof Error
              ? error.message
              : 'Settings could not be loaded.',
        })),
    ])
    return { ...health, configuration }
  },
  head: () => ({ meta: [{ title: 'Settings · phlo' }] }),
  component: SettingsPage,
})

const connectionNames: Record<string, string> = {
  nessie: 'Nessie catalog',
  dagster: 'Dagster',
  postgres: 'Postgres',
  minio: 'Object store',
  trino: 'Trino',
  'phlo-api': 'Phlo API',
}

function saveStatus(
  pending: boolean,
  dirty: boolean,
  saved: boolean,
  loaded: Settings | undefined,
  version: number,
) {
  if (pending) return 'Saving…'
  if (dirty) return 'Unsaved changes'
  if (saved) return 'Saved'
  return loaded
    ? `Saved configuration · version ${version}`
    : 'Configuration unavailable'
}

function SettingsPage() {
  const { overview, services, environmentBinding, configuration } =
    Route.useLoaderData()
  const loaded = configuration.value
  const [s, setS] = React.useState<Settings>(loaded?.settings ?? emptySettings)
  const [savedS, setSavedS] = React.useState<Settings>(
    loaded?.settings ?? emptySettings,
  )
  const [version, setVersion] = React.useState(loaded?.version ?? 0)
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string | null>(configuration.error)
  const [saved, setSaved] = React.useState(false)
  const dirty = JSON.stringify(s) !== JSON.stringify(savedS)
  const set = <TKey extends keyof Settings>(key: TKey, value: Settings[TKey]) =>
    setS((previous) => ({ ...previous, [key]: value }))

  React.useEffect(() => {
    if (!loaded || loaded.version <= version) return
    if (dirty) {
      setError(
        `Saved settings changed to version ${loaded.version} while you were editing. Discard or reload before saving.`,
      )
      return
    }
    setS(loaded.settings)
    setSavedS(loaded.settings)
    setVersion(loaded.version)
    setError(null)
  }, [loaded?.version])

  const save = async () => {
    const validated = settingsSchema.safeParse(s)
    if (!validated.success) {
      setError(validated.error.issues.map((issue) => issue.message).join(' '))
      return
    }
    setPending(true)
    setError(null)
    try {
      const result = await saveSettings({
        data: {
          expectedVersion: version,
          settings: s,
          idempotencyKey: crypto.randomUUID(),
        },
      })
      setS(result.settings)
      setSavedS(result.settings)
      setVersion(result.version)
      setSaved(true)
    } catch (cause) {
      setError(
        cause instanceof Error ? cause.message : 'Settings could not be saved.',
      )
    } finally {
      setPending(false)
    }
  }

  return (
    <SettingsFrame
      header={
        <PageHeader
          title="Settings"
          meta={
            <>
              Lakehouse · <Mono>{overview.env}</Mono>
            </>
          }
          actions={
            <>
              <span
                className="hidden text-[13px] text-muted-foreground sm:inline"
                aria-live="polite"
              >
                {saveStatus(pending, dirty, saved, loaded?.settings, version)}
              </span>
              <Button
                variant="outline"
                className="h-10 lg:h-8"
                disabled={!dirty || pending}
                onClick={() => {
                  setS(savedS)
                  setError(null)
                }}
              >
                Discard
              </Button>
              <Button
                className="h-10 lg:h-8"
                disabled={!dirty || pending || !loaded}
                onClick={save}
              >
                {pending ? <Loader2Icon className="animate-spin" /> : null} Save
                changes
              </Button>
            </>
          }
        />
      }
    >
      <div className="flex min-h-0 flex-1 flex-col gap-4 overflow-y-auto p-4 lg:px-6 lg:py-5">
        {error ? (
          <div
            role="alert"
            className="rounded-lg border border-destructive/40 bg-destructive/5 px-4 py-3 text-sm text-destructive"
          >
            {error}
          </div>
        ) : null}
        <EnvironmentBindingCard
          env={overview.env}
          binding={environmentBinding}
        />
        <SettingsCard
          title="Service health"
          description="Environment-bound health checks. Connection strings and credentials stay on the server."
          inline
          className="gap-2"
        >
          <div className="flex flex-col">
            {environmentServices(services).map((service) => {
              return (
                <ConnectionRow
                  key={service.id}
                  name={connectionNames[service.id] ?? service.id}
                  service={service}
                  detail={
                    service.response_time_seconds == null
                      ? undefined
                      : `${service.response_time_seconds.toFixed(3)} s`
                  }
                />
              )
            })}
          </div>
        </SettingsCard>

        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
          <SettingsCard
            title="Freshness targets"
            description="Layer defaults apply when an asset has no declared SLA or incident-policy override. Missing observations remain unknown."
          >
            {(['bronze', 'silver', 'gold'] as const).map((l) => (
              <NumberRow
                key={l}
                label={
                  <span className="flex items-center gap-2.5">
                    <LayerSwatch layer={l} size="lg" />
                    {l[0].toUpperCase() + l.slice(1)}
                  </span>
                }
                labelWidth="w-[100px]"
                value={s.sla[l]}
                onChange={(v) => set('sla', { ...s.sla, [l]: v })}
                unit="min"
              />
            ))}
            <Split>
              <CheckLine
                checked={s.openIncidentOnBreach}
                onCheckedChange={(v) => set('openIncidentOnBreach', v)}
              >
                Open an incident when a table breaches its SLA
              </CheckLine>
              <CheckLine
                checked={s.holdDownstream}
                onCheckedChange={(v) => set('holdDownstream', v)}
              >
                Hold downstream jobs while an upstream table is stale
              </CheckLine>
            </Split>
          </SettingsCard>

          <SettingsCard
            title="Alerts"
            description="Uses configured alert providers. Digests run through the Dagster sensor in UTC; owner and consumer delivery require operator-configured recipients."
          >
            <TextRow
              label="Chat channel"
              labelWidth="w-[110px]"
              value={s.chat}
              onChange={(v) => set('chat', v)}
            />
            <TextRow
              label="Email digest"
              labelWidth="w-[110px]"
              value={s.digest}
              onChange={(v) => set('digest', v)}
            />
            <Split>
              <CheckLine
                checked={s.notifyOwners}
                onCheckedChange={(v) => set('notifyOwners', v)}
              >
                Notify asset owners directly
              </CheckLine>
              <CheckLine
                checked={s.notifyConsumers}
                onCheckedChange={(v) => set('notifyConsumers', v)}
              >
                Tell downstream consumers when their data is stale
              </CheckLine>
            </Split>
          </SettingsCard>

          <SettingsCard
            title="Table maintenance"
            description="The bound Dagster policy sensor evaluates nightly at 02:00 UTC. File size is the small-file threshold. Retention and orphan cleanup produce protected plans; execution requires operator approval."
          >
            <NumberRow
              label="Target file size"
              labelWidth="w-[150px]"
              value={s.fileSize}
              onChange={(v) => set('fileSize', v)}
              unit="MB"
            />
            <NumberRow
              label="Expire snapshots after"
              labelWidth="w-[150px]"
              value={s.expireDays}
              onChange={(v) => set('expireDays', v)}
              unit="days"
            />
            <TextRow
              label="Orphan file cleanup"
              labelWidth="w-[150px]"
              value={s.orphan}
              onChange={(v) => set('orphan', v)}
            />
            <Split>
              <CheckLine
                checked={s.keepTagged}
                onCheckedChange={(v) => set('keepTagged', v)}
              >
                Keep snapshots that a release tag points to
              </CheckLine>
              <p className="m-0 text-xs text-muted-foreground">
                The table provider always protects referenced snapshots, even
                when this preference is off.
              </p>
              <CheckLine
                checked={s.compactNightly}
                onCheckedChange={(v) => set('compactNightly', v)}
              >
                Compact nightly when a table has 200+ small files
              </CheckLine>
            </Split>
          </SettingsCard>

          <SettingsCard
            title="Audit & data integrity"
            description={
              <>
                Saved preferences only; branch protection and audit enforcement
                are configured by their owning services.
              </>
            }
          >
            <CheckLine
              checked={s.protectMain}
              onCheckedChange={(v) => set('protectMain', v)}
            >
              Protect <Mono>main</Mono> from direct writes
            </CheckLine>
            <CheckLine
              checked={s.requireReason}
              onCheckedChange={(v) => set('requireReason', v)}
            >
              Require a reason on every merge into <Mono>main</Mono>
            </CheckLine>
            <CheckLine
              checked={s.signTags}
              onCheckedChange={(v) => set('signTags', v)}
            >
              Require an e-signature to create a release tag
            </CheckLine>
            <CheckLine
              checked={s.secondReviewer}
              onCheckedChange={(v) => set('secondReviewer', v)}
            >
              Require a second reviewer for gold-layer merges
            </CheckLine>
            <div className="border-t border-line-soft pt-3">
              <TextRow
                label="Keep audit log for"
                labelWidth="w-[150px]"
                value={s.retention}
                onChange={(v) => set('retention', v)}
                unit="years"
                narrow
                mono
              />
            </div>
          </SettingsCard>
        </div>
      </div>
    </SettingsFrame>
  )
}

function EnvironmentBindingCard({
  env,
  binding,
}: {
  env: Awaited<ReturnType<typeof getShell>>['overview']['env']
  binding: Awaited<ReturnType<typeof getShell>>['environmentBinding']
}) {
  return (
    <SettingsCard
      title={`Where ${env} connects`}
      description="The job definitions and table reference used in this environment."
    >
      <div className="grid gap-4 sm:grid-cols-2">
        <dl className="m-0 flex min-w-0 flex-col gap-1">
          <dt className="text-sm text-muted-foreground">Jobs</dt>
          <dd className="m-0 break-all font-mono text-sm">
            {binding.dagsterLocation ?? 'Not reported by the API'}
          </dd>
          <dd className="m-0 text-sm text-muted-foreground">
            Dagster code location
          </dd>
        </dl>
        <dl className="m-0 flex min-w-0 flex-col gap-1">
          <dt className="text-sm text-muted-foreground">Tables</dt>
          <dd className="m-0 break-all font-mono text-sm">
            {binding.nessieRef ?? 'Not reported by the API'}
          </dd>
          <dd className="m-0 text-sm text-muted-foreground">
            Nessie reference
          </dd>
        </dl>
      </div>
      <p className="m-0 text-sm text-muted-foreground">
        These connections are read-only here. Save changes applies to the
        preferences below, not to these connections.
      </p>
      <details className="border-t border-line-soft pt-3 text-sm">
        <summary className="cursor-pointer text-muted-foreground hover:text-foreground">
          Deployment details
        </summary>
        <p className="mt-2 mb-0 text-muted-foreground">
          This Observatory supports prod and staging. To change their
          connections, an operator updates <code>PHLO_V1_ENVIRONMENTS</code> in
          the API deployment. This page cannot create environments or provision
          services.
          <code> PHLO_ENVIRONMENT</code> only labels a Phlo runtime.
        </p>
      </details>
    </SettingsCard>
  )
}

function SettingsCard({
  title,
  description,
  inline,
  className,
  children,
}: {
  title: React.ReactNode
  description: React.ReactNode
  inline?: boolean
  className?: string
  children: React.ReactNode
}) {
  return (
    <section
      className={cn(
        'flex flex-col gap-3 rounded-xl border border-border-card bg-card px-4 py-4 lg:px-5 lg:py-[18px]',
        className,
      )}
    >
      <div
        className={cn(
          'flex gap-x-2.5 gap-y-0.5',
          inline ? 'flex-wrap items-baseline pb-1' : 'flex-col',
        )}
      >
        <CardTitle>{title}</CardTitle>
        <CardDescription>{description}</CardDescription>
      </div>
      {children}
    </section>
  )
}

function Split({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-2.5 border-t border-line-soft pt-3">
      {children}
    </div>
  )
}

function NumberRow(props: {
  label: React.ReactNode
  labelWidth: string
  value: string
  onChange: (v: string) => void
  unit: string
}) {
  return <TextRow {...props} narrow mono inputMode="numeric" />
}

function TextRow({
  label,
  labelWidth,
  value,
  onChange,
  unit,
  narrow,
  mono,
  inputMode,
}: {
  label: React.ReactNode
  labelWidth: string
  value: string
  onChange: (v: string) => void
  unit?: string
  narrow?: boolean
  mono?: boolean
  inputMode?: 'numeric'
}) {
  return (
    <Field className="flex-row items-center gap-2.5">
      <FieldLabel
        className={cn('shrink-0 text-sm font-normal text-text-2', labelWidth)}
      >
        {label}
      </FieldLabel>
      <Input
        value={value}
        inputMode={inputMode}
        onChange={(e) => onChange(e.target.value)}
        className={cn(
          'h-10 sm:h-8',
          narrow ? 'w-[84px]' : 'min-w-0 flex-1',
          mono && 'font-mono text-[13.5px]',
        )}
      />
      {unit ? (
        <span className="text-[13px] text-muted-foreground">{unit}</span>
      ) : null}
    </Field>
  )
}

function ConnectionRow({
  name,
  service,
  detail,
}: {
  name: string
  service: ObservatoryServiceList['items'][number]
  detail?: string
}) {
  const router = useRouter()
  const [testing, setTesting] = React.useState(false)
  const state = serviceHealthLabel(service)
  const source = service.health_origin
    ? `Health host: ${service.health_origin}`
    : service.reason?.startsWith('docker_')
      ? 'Container health check'
      : 'Environment health check'
  return (
    <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-4 gap-y-1 border-t border-line-soft py-2.5 md:h-11 md:grid-cols-[150px_minmax(0,1fr)_190px_64px] md:py-0">
      <span className="text-sm">{name}</span>
      <span
        className="col-start-1 row-start-2 truncate text-[13px] text-muted-foreground md:col-start-auto md:row-start-auto"
        title={source}
      >
        {source}
      </span>
      <span
        className="col-start-1 row-start-3 flex items-center gap-2 text-[13.5px] md:col-start-auto md:row-start-auto"
        aria-live="polite"
      >
        <Dot tone={serviceHealthTone(service)} />
        {state}
        {detail ? (
          <span className="text-[13px] text-muted-foreground">· {detail}</span>
        ) : null}
      </span>
      <Button
        variant="outline"
        size="sm"
        className="col-start-2 row-span-3 row-start-1 h-10 md:col-start-auto md:row-span-1 md:row-start-auto md:h-7"
        disabled={testing}
        aria-label={`Refresh ${name} health check`}
        onClick={async () => {
          setTesting(true)
          try {
            await router.invalidate()
          } finally {
            setTesting(false)
          }
        }}
      >
        {testing ? <Loader2Icon className="animate-spin" /> : 'Refresh'}
      </Button>
    </div>
  )
}
