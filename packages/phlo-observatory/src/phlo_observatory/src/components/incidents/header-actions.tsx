/** Renders acknowledgement, subscription, and resolution actions for an incident. */
import * as React from 'react'
import { useRouter } from '@tanstack/react-router'
import { BellIcon, BellOffIcon, EllipsisIcon, LinkIcon } from 'lucide-react'
import type { IncidentRecord } from '@/lib/data/api/incidents'
import { getIncidentSubscription } from '@/lib/data/api/notifications'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/menu'
import { Textarea } from '@/components/ui/input'
import {
  clearIncidentOperationKey,
  incidentOperationKey,
  resolveIncident,
  setIncidentSubscription,
  updateIncident,
} from '@/lib/data/api/incidents'

export function IncidentActions({
  env,
  incident,
}: {
  env: string
  incident: IncidentRecord
}) {
  const router = useRouter()
  const [subscribed, setSubscribed] = React.useState<boolean>()
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string>()
  const [comment, setComment] = React.useState('')
  const [reopen, setReopen] = React.useState(false)
  const [copied, setCopied] = React.useState(false)
  const [subscriptionLoading, setSubscriptionLoading] = React.useState(true)
  React.useEffect(() => {
    let active = true
    setSubscribed(undefined)
    setSubscriptionLoading(true)
    void getIncidentSubscription({ data: { env, id: incident.id } })
      .then((result) => {
        if (active) setSubscribed(result.subscribed)
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Subscription unavailable.',
          )
      })
      .finally(() => {
        if (active) setSubscriptionLoading(false)
      })
    return () => {
      active = false
    }
  }, [env, incident.id])
  async function run(action: () => Promise<unknown>) {
    setPending(true)
    setError(undefined)
    try {
      await action()
      await router.invalidate()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Action failed.')
    } finally {
      setPending(false)
    }
  }
  const updateStatus = (status: 'open' | 'acknowledged') =>
    run(() =>
      updateIncident({
        data: {
          env,
          id: incident.id,
          version: incident.version,
          idempotency_key: incidentOperationKey(
            env,
            incident.id,
            'status',
            `${incident.version}:${status}`,
          ),
          update: { status },
        },
      }),
    )
  return (
    <>
      <Button
        variant="outline"
        className="h-10 lg:h-8"
        disabled={pending || subscriptionLoading}
        aria-pressed={subscribed}
        onClick={() =>
          run(async () => {
            if (subscribed === undefined) {
              setSubscribed(
                (
                  await getIncidentSubscription({
                    data: { env, id: incident.id },
                  })
                ).subscribed,
              )
              return
            }
            const next = subscribed !== true
            const result = await setIncidentSubscription({
              data: {
                env,
                id: incident.id,
                subscribed: next,
                idempotency_key: incidentOperationKey(
                  env,
                  incident.id,
                  'subscription',
                  String(next),
                ),
              },
            })
            clearIncidentOperationKey(
              env,
              incident.id,
              'subscription',
              String(next),
            )
            setSubscribed(result.subscribed)
          })
        }
      >
        {subscribed === true ? (
          <BellIcon />
        ) : subscribed === false ? (
          <BellOffIcon />
        ) : (
          <BellIcon />
        )}
        <span className="lg:hidden">
          {subscriptionLoading
            ? 'Loading…'
            : subscribed
              ? 'Subscribed'
              : 'Subscribe'}
        </span>
        <span className="hidden lg:inline">
          {subscribed === undefined
            ? subscriptionLoading
              ? 'Loading subscription…'
              : 'Retry subscription'
            : subscribed
              ? 'Subscribed'
              : 'Not subscribed · Subscribe'}
        </span>
      </Button>
      {incident.status === 'open' ? (
        <Button disabled={pending} onClick={() => updateStatus('acknowledged')}>
          Acknowledge
        </Button>
      ) : null}
      {incident.status === 'resolved' ? (
        <Popover open={reopen} onOpenChange={setReopen}>
          <PopoverTrigger
            render={
              <Button
                variant="outline"
                className="h-10 lg:h-8"
                disabled={pending}
              />
            }
          >
            Reopen
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="flex w-[300px] flex-col gap-3 p-4"
          >
            <div className="text-sm font-medium">Reopen #{incident.id}?</div>
            <p className="m-0 text-[13px] leading-snug text-muted-foreground">
              The incident returns to Open. Persisted evidence and follow-ups
              remain attached.
            </p>
            <div className="flex justify-end gap-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => setReopen(false)}
              >
                Cancel
              </Button>
              <Button
                size="sm"
                disabled={pending}
                onClick={() => {
                  setReopen(false)
                  updateStatus('open')
                }}
              >
                Reopen
              </Button>
            </div>
          </PopoverContent>
        </Popover>
      ) : null}
      <DropdownMenu>
        <DropdownMenuTrigger
          aria-label="More actions"
          className="inline-flex size-10 cursor-pointer items-center justify-center rounded-lg border border-border bg-raised text-text-3 hover:bg-soft lg:size-8"
        >
          <EllipsisIcon className="size-4" />
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            onClick={() => {
              void navigator.clipboard.writeText(window.location.href)
              setCopied(true)
            }}
          >
            <LinkIcon className="size-3.5" />{' '}
            {copied ? 'Link copied' : 'Copy link'}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {incident.status === 'acknowledged' ? (
        <Popover>
          <PopoverTrigger render={<Button disabled={pending} />}>
            Resolve…
          </PopoverTrigger>
          <PopoverContent
            align="end"
            className="flex w-[340px] flex-col gap-3 p-4"
          >
            <label className="text-sm font-medium">
              Resolution comment
              <Textarea
                className="mt-2"
                rows={3}
                value={comment}
                onChange={(event) => setComment(event.target.value)}
              />
            </label>
            <p className="m-0 text-xs text-muted-foreground">
              Resolution creates an action-bound electronic signature. Recent
              MFA is required.
            </p>
            <Button
              disabled={!comment.trim() || pending}
              onClick={() =>
                run(() =>
                  resolveIncident({
                    data: {
                      env,
                      id: incident.id,
                      version: incident.version,
                      comment,
                      idempotency_key: incidentOperationKey(
                        env,
                        incident.id,
                        'resolve',
                        `${incident.version}:${comment.trim()}`,
                      ),
                    },
                  }),
                )
              }
            >
              Sign and resolve
            </Button>
          </PopoverContent>
        </Popover>
      ) : null}
      {error ? (
        <span role="alert" className="max-w-56 text-xs text-bad-text">
          {error}
        </span>
      ) : null}
    </>
  )
}
