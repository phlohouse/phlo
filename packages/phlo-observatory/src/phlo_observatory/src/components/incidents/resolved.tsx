/** Renders resolved incident details, timeline events, and follow-ups. */
import * as React from 'react'
import { Link, useRouter } from '@tanstack/react-router'
import type {
  IncidentFollowUp,
  IncidentRecord,
  IncidentTimelineEvent,
} from '@/lib/data/api/incidents'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Checkbox } from '@/components/ui/checkbox'
import { Input, Textarea } from '@/components/ui/input'
import { EmptyState } from '@/components/phlo/states'
import { Eyebrow, KeyValues } from '@/components/phlo/page'
import {
  clearIncidentOperationKey,
  createFollowUp,
  incidentOperationKey,
  updateFollowUp,
  updateIncident,
} from '@/lib/data/api/incidents'

const formatDate = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))
const payloadText = (payload: IncidentTimelineEvent['payload']) => {
  if (typeof payload.text === 'string') return payload.text
  return Object.keys(payload).length
    ? JSON.stringify(payload, null, 2)
    : undefined
}

export function IncidentDetail({
  env,
  incident,
  timeline,
  followUps,
}: {
  env: 'prod' | 'staging'
  incident: IncidentRecord
  timeline: Array<IncidentTimelineEvent>
  followUps: Array<IncidentFollowUp>
}) {
  const router = useRouter()
  const [comment, setComment] = React.useState('')
  const [owner, setOwner] = React.useState(incident.owner ?? '')
  const [followUp, setFollowUp] = React.useState('')
  const [due, setDue] = React.useState('')
  const [pending, setPending] = React.useState(false)
  const [error, setError] = React.useState<string>()
  async function run(action: () => Promise<unknown>, clear?: () => void) {
    if (pending) return
    setPending(true)
    setError(undefined)
    try {
      await action()
      clear?.()
      await router.invalidate()
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : 'Action failed.')
    } finally {
      setPending(false)
    }
  }
  const update = (value: { owner?: string | null; comment?: string }) =>
    updateIncident({
      data: {
        env,
        id: incident.id,
        version: incident.version,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'update',
          `${incident.version}:${JSON.stringify(value)}`,
        ),
        update: value,
      },
    })
  const changeFollowUp = async (id: string, completed: boolean) => {
    const intent = `${id}:${completed}`
    await updateFollowUp({
      data: {
        env,
        id: incident.id,
        follow_up_id: id,
        completed,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'follow-up-update',
          intent,
        ),
      },
    })
    clearIncidentOperationKey(env, incident.id, 'follow-up-update', intent)
  }
  const addFollowUp = async (description: string, dueAt: string | null) => {
    const intent = `${description}:${dueAt}`
    await createFollowUp({
      data: {
        env,
        id: incident.id,
        description,
        due_at: dueAt,
        idempotency_key: incidentOperationKey(
          env,
          incident.id,
          'follow-up-create',
          intent,
        ),
      },
    })
    clearIncidentOperationKey(env, incident.id, 'follow-up-create', intent)
  }
  return (
    <div className="flex min-h-0 flex-1 flex-col overflow-y-auto lg:grid lg:grid-cols-[400px_minmax(0,1fr)] lg:overflow-hidden">
      <aside
        aria-label="Summary"
        className="border-b border-line p-4 lg:overflow-y-auto lg:border-r lg:border-b-0 lg:p-6"
      >
        <div className="mb-5 flex flex-wrap items-center gap-2">
          <Badge
            variant={
              incident.status === 'resolved'
                ? 'ok'
                : incident.status === 'acknowledged'
                  ? 'warn'
                  : 'bad'
            }
          >
            {incident.status}
          </Badge>
          <Badge variant="outline">{incident.kind}</Badge>
        </div>
        <KeyValues
          keyWidth={90}
          items={[
            [
              'Asset',
              <Link
                to="/assets/$assetId"
                params={{ assetId: incident.asset_id }}
                search={{ env }}
                className="break-all font-mono text-xs"
              >
                {incident.asset_id}
              </Link>,
            ],
            ['Owner', incident.owner ?? 'Unassigned'],
            ['Created', formatDate(incident.created_at)],
            ['Updated', formatDate(incident.updated_at)],
            ['Version', String(incident.version)],
          ]}
        />
        <div className="mt-6 flex flex-col gap-3 border-t border-line pt-5">
          <Eyebrow>Update incident</Eyebrow>
          <label className="text-sm">
            Owner
            <Input
              className="mt-1.5"
              value={owner}
              maxLength={512}
              onChange={(event) => setOwner(event.target.value)}
            />
          </label>
          <Button
            variant="outline"
            disabled={pending || owner === (incident.owner ?? '')}
            onClick={() => run(() => update({ owner: owner.trim() || null }))}
          >
            Save owner
          </Button>
          <label className="text-sm">
            Comment
            <Textarea
              className="mt-1.5"
              rows={3}
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          </label>
          <Button
            disabled={pending || !comment.trim()}
            onClick={() =>
              run(
                () => update({ comment: comment.trim() }),
                () => setComment(''),
              )
            }
          >
            Add comment
          </Button>
          {error ? (
            <p role="alert" className="m-0 text-sm text-bad-text">
              {error}
            </p>
          ) : null}
        </div>
      </aside>
      <main className="flex min-h-0 flex-col gap-7 p-4 lg:overflow-y-auto lg:p-7">
        <section>
          <h2 className="mb-3 text-base font-medium">Timeline</h2>
          {timeline.length ? (
            <ol className="m-0 list-none border-l border-line p-0 pl-5">
              {timeline.map((event) => (
                <li key={event.id} className="relative pb-5 last:pb-0">
                  <span className="absolute top-1 -left-[24.5px] size-2 rounded-full bg-primary" />
                  <div className="flex flex-wrap gap-x-2 text-sm">
                    <strong>{event.kind.replaceAll('_', ' ')}</strong>
                    <span className="text-muted-foreground">
                      {event.actor} · {formatDate(event.occurred_at)}
                    </span>
                  </div>
                  {payloadText(event.payload) ? (
                    <pre className="mt-2 overflow-x-auto whitespace-pre-wrap rounded-lg bg-sunken p-3 text-xs text-text-2">
                      {payloadText(event.payload)}
                    </pre>
                  ) : null}
                </li>
              ))}
            </ol>
          ) : (
            <EmptyState title="No timeline events">
              No persisted activity is available for this incident.
            </EmptyState>
          )}
        </section>
        <section>
          <div className="mb-3 flex items-center">
            <h2 className="m-0 text-base font-medium">Follow-ups</h2>
            <span className="ml-auto text-xs text-muted-foreground">
              {followUps.filter((item) => item.completed_at).length} of{' '}
              {followUps.length} complete
            </span>
          </div>
          {followUps.length ? (
            <ul className="m-0 list-none rounded-xl border border-border-card p-0">
              {followUps.map((item) => (
                <li
                  key={item.id}
                  className="flex items-start gap-3 border-b border-line-soft p-3 last:border-0"
                >
                  <Checkbox
                    checked={item.completed_at !== null}
                    disabled={pending}
                    aria-label={`Complete follow-up: ${item.description}`}
                    onCheckedChange={(checked) =>
                      run(() => changeFollowUp(item.id, checked === true))
                    }
                  />
                  <span className="min-w-0 flex-1 text-sm">
                    <span
                      className={
                        item.completed_at
                          ? 'text-muted-foreground line-through'
                          : ''
                      }
                    >
                      {item.description}
                    </span>
                    {item.due_at ? (
                      <span className="mt-1 block text-xs text-muted-foreground">
                        Due {formatDate(item.due_at)}
                      </span>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-sm text-muted-foreground">
              No follow-ups recorded.
            </p>
          )}
          <form
            className="mt-3 flex flex-col gap-2 sm:flex-row"
            onSubmit={(event) => {
              event.preventDefault()
              const description = followUp.trim()
              const dueAt = due
                ? new Date(`${due}T00:00:00`).toISOString()
                : null
              run(
                () => addFollowUp(description, dueAt),
                () => {
                  setFollowUp('')
                  setDue('')
                },
              )
            }}
          >
            <Input
              aria-label="Follow-up description"
              required
              value={followUp}
              onChange={(event) => setFollowUp(event.target.value)}
              placeholder="Add a follow-up"
            />
            <Input
              aria-label="Due date"
              type="date"
              value={due}
              onChange={(event) => setDue(event.target.value)}
              className="sm:w-44"
            />
            <Button type="submit" disabled={pending || !followUp.trim()}>
              Add
            </Button>
          </form>
        </section>
      </main>
    </div>
  )
}
