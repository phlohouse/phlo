/** Displays real incident events with durable actor-owned read state. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import { BellIcon, CheckIcon, ClockIcon, RefreshCwIcon } from 'lucide-react'
import type {
  NotificationItem,
  NotificationPage,
  NotificationView,
} from '@/lib/data/api/notifications'
import {
  getNotifications,
  markNotificationsRead,
} from '@/lib/data/api/notifications'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'

const views: Array<{ value: NotificationView; label: string }> = [
  { value: 'for_you', label: 'For you' },
  { value: 'watching', label: 'Watching' },
  { value: 'all', label: 'All' },
]
const eventNames: Record<string, string> = {
  signal: 'Incident detected',
  created: 'Incident opened',
  owner: 'Owner changed',
  owner_changed: 'Owner changed',
  status: 'Status changed',
  status_changed: 'Status changed',
  comment: 'Comment added',
  resolution_comment: 'Resolution recorded',
  schema_decision: 'Schema decision recorded',
  query_evidence: 'Query evidence pinned',
  subscription: 'Subscription changed',
}
const dateTime = (value: string) =>
  new Intl.DateTimeFormat(undefined, {
    dateStyle: 'medium',
    timeStyle: 'short',
  }).format(new Date(value))

function useNotificationInbox(env: string) {
  const [view, setView] = React.useState<NotificationView>('for_you')
  const [unreadOnly, setUnreadOnly] = React.useState(false)
  const [cursors, setCursors] = React.useState<Array<string>>([])
  const after = cursors.at(-1)
  const [page, setPage] = React.useState<NotificationPage>()
  const [error, setError] = React.useState<string>()
  const [pending, setPending] = React.useState(false)
  const [revision, setRevision] = React.useState(0)
  const mounted = React.useRef(true)
  React.useEffect(() => {
    mounted.current = true
    return () => {
      mounted.current = false
    }
  }, [])
  React.useEffect(() => {
    let active = true
    setPending(true)
    setError(undefined)
    setPage(undefined)
    void getNotifications({
      data: {
        env,
        view,
        ...(after ? { after } : {}),
        ...(unreadOnly ? { unread_only: true } : {}),
      },
    })
      .then((value) => {
        if (active) setPage(value)
      })
      .catch((caught: unknown) => {
        if (active)
          setError(
            caught instanceof Error
              ? caught.message
              : 'Notifications unavailable.',
          )
      })
      .finally(() => {
        if (active) setPending(false)
      })
    return () => {
      active = false
    }
  }, [env, view, revision, after, unreadOnly])
  async function mark(ids: Array<string>) {
    if (!ids.length || pending) return
    setPending(true)
    setError(undefined)
    try {
      const result = await markNotificationsRead({
        data: { env, eventIds: ids },
      })
      if (mounted.current)
        setPage((current) =>
          current
            ? {
                ...current,
                items: current.items
                  .map((item) =>
                    result.event_ids.includes(item.id)
                      ? { ...item, read: true }
                      : item,
                  )
                  .filter((item) => !unreadOnly || !item.read),
                unread: current.items.filter(
                  (item) => !item.read && !result.event_ids.includes(item.id),
                ).length,
              }
            : current,
        )
    } catch (caught) {
      if (mounted.current)
        setError(
          caught instanceof Error
            ? caught.message
            : 'Could not mark notifications read.',
        )
    } finally {
      if (mounted.current) setPending(false)
    }
  }
  return {
    view,
    setView: (next: NotificationView) => {
      setCursors([])
      setView(next)
    },
    unreadOnly,
    setUnreadOnly: (next: boolean) => {
      setCursors([])
      setUnreadOnly(next)
    },
    hasPrevious: cursors.length > 0,
    previous: () => setCursors((items) => items.slice(0, -1)),
    next: () => {
      if (page?.next_cursor)
        setCursors((items) => [...items, page.next_cursor!])
    },
    page,
    error,
    pending,
    refresh: () => {
      setCursors([])
      setRevision((value) => value + 1)
    },
    mark,
  }
}

function NotificationRow({
  item,
  env,
  pending,
  onRead,
  onOpen,
}: {
  item: NotificationItem
  env: string
  pending: boolean
  onRead: () => void
  onOpen: () => void
}) {
  return (
    <li
      className={`relative flex items-start gap-3 border-b border-line-soft px-5 py-4 ${item.read ? '' : 'bg-raised/50'}`}
    >
      {!item.read ? (
        <span
          className="absolute top-6 left-2 size-1.5 rounded-full bg-link"
          aria-label="Unread"
        />
      ) : null}
      <span className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-md bg-primary-soft text-link">
        <ClockIcon className="size-3.5" />
      </span>
      <div className="min-w-0 flex-1">
        <p className="m-0 text-[13px] leading-relaxed">
          <span className="font-medium">
            {eventNames[item.kind] ?? item.kind.replaceAll('_', ' ')}
          </span>{' '}
          · {item.title}
        </p>
        <p className="mt-1 mb-0 break-all font-mono text-xs text-text-3">
          {item.asset_id}
        </p>
        <p className="mt-1 mb-0 text-xs leading-relaxed text-muted-foreground">
          {dateTime(item.occurred_at)} · {item.actor}
        </p>
        <div className="mt-2 flex items-center gap-3">
          <Link
            to="/incidents/$incidentId"
            params={{ incidentId: item.incident_id }}
            search={{ env }}
            className="text-xs"
            onClick={onOpen}
          >
            Open incident
          </Link>
          {!item.read ? (
            <button
              type="button"
              disabled={pending}
              onClick={onRead}
              className="inline-flex cursor-pointer items-center gap-1 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <CheckIcon className="size-3" />
              Mark read
            </button>
          ) : null}
        </div>
      </div>
    </li>
  )
}

function NotificationList({
  page,
  view,
  error,
  env,
  pending,
  mark,
  onOpen,
}: {
  page?: NotificationPage
  view: NotificationView
  error?: string
  env: string
  pending: boolean
  mark: (ids: Array<string>) => Promise<void>
  onOpen: () => void
}) {
  return (
    <>
      {page?.items.length ? (
        <ul className="m-0 list-none p-0">
          {page.items.map((item) => (
            <NotificationRow
              key={item.id}
              item={item}
              env={env}
              pending={pending}
              onRead={() => void mark([item.id])}
              onOpen={onOpen}
            />
          ))}
        </ul>
      ) : page && !error ? (
        <div className="space-y-2 p-5">
          <h3 className="m-0 text-sm font-medium">
            No notifications{' '}
            {view === 'for_you'
              ? 'assigned to you'
              : view === 'watching'
                ? 'from watched incidents'
                : 'in this environment'}
          </h3>
          <p className="m-0 text-sm leading-relaxed text-muted-foreground">
            {view === 'for_you'
              ? 'For you includes incidents whose owner exactly matches your authenticated identity.'
              : view === 'watching'
                ? 'Subscribe to an incident to follow its recorded activity.'
                : 'No authorized incident activity was returned.'}
          </p>
        </div>
      ) : null}
    </>
  )
}

function InboxBody({ env, onOpen }: { env: string; onOpen: () => void }) {
  const {
    view,
    setView,
    page,
    error,
    pending,
    refresh,
    mark,
    unreadOnly,
    setUnreadOnly,
    hasPrevious,
    previous,
    next,
  } = useNotificationInbox(env)
  return (
    <>
      <div className="flex flex-wrap items-center gap-2 border-b border-line px-4 py-2">
        <div
          role="group"
          aria-label="Notification filter"
          className="flex gap-1"
        >
          {views.map((item) => (
            <button
              type="button"
              key={item.value}
              aria-pressed={view === item.value}
              disabled={pending}
              onClick={() => setView(item.value)}
              className={`cursor-pointer rounded-md px-3 py-1.5 text-xs ${view === item.value ? 'bg-soft text-foreground' : 'text-muted-foreground hover:bg-raised'}`}
            >
              {item.label}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-1.5 text-xs">
          <input
            type="checkbox"
            checked={unreadOnly}
            disabled={pending}
            onChange={(event) => setUnreadOnly(event.target.checked)}
          />
          Unread only
        </label>
        <button
          type="button"
          disabled={pending || !page?.unread}
          onClick={() =>
            void mark(
              page?.items.filter((item) => !item.read).map((item) => item.id) ??
                [],
            )
          }
          className="ml-auto cursor-pointer text-xs text-link disabled:opacity-50"
        >
          Mark all read
        </button>
      </div>
      <div className="flex items-center justify-between border-b border-line-soft px-5 py-2">
        <span className="text-xs text-muted-foreground">
          {page
            ? `${page.unread} unread in this list`
            : 'Loading notifications…'}
        </span>
        <Button
          size="icon-sm"
          variant="ghost"
          aria-label="Refresh notifications"
          disabled={pending}
          onClick={refresh}
        >
          <RefreshCwIcon className="size-3.5" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto">
        {error ? (
          <div role="alert" className="space-y-3 p-5">
            <p className="m-0 text-sm text-bad-text">{error}</p>
            <Button variant="outline" size="sm" onClick={refresh}>
              Retry
            </Button>
          </div>
        ) : null}
        {pending && !page ? (
          <p role="status" className="p-5 text-sm text-muted-foreground">
            Loading incident activity…
          </p>
        ) : null}
        <NotificationList
          page={page}
          view={view}
          error={error}
          env={env}
          pending={pending}
          mark={mark}
          onOpen={onOpen}
        />
      </div>
      <footer className="space-y-1 border-t border-line px-5 py-4 text-xs leading-relaxed text-muted-foreground">
        <div className="mb-3 flex items-center justify-between gap-3">
          <Button
            variant="outline"
            size="sm"
            disabled={pending || !hasPrevious}
            onClick={previous}
          >
            Newer activity
          </Button>
          <Button
            variant="outline"
            size="sm"
            disabled={pending || !page?.next_cursor}
            onClick={next}
          >
            Older activity
          </Button>
        </div>
        <p className="m-0">
          Actual incident activity in {env} over the last 30 days. Read state is
          private to your identity.
        </p>
        <p className="m-0">
          {page?.truncated
            ? page.next_cursor
              ? 'More activity is available on the next page.'
              : 'This list is bounded. Open Incidents for more activity.'
            : 'Up to 100 authorized events are shown.'}{' '}
          Mark all read applies to this list.
        </p>
      </footer>
    </>
  )
}

export function NotificationsButton({ env }: { env: string }) {
  const [open, setOpen] = React.useState(false)
  return (
    <>
      <button
        type="button"
        aria-label="Notifications"
        aria-haspopup="dialog"
        aria-expanded={open}
        onClick={() => setOpen(true)}
        className="inline-flex size-9 shrink-0 cursor-pointer items-center justify-center rounded-lg border border-border bg-card text-text-2 hover:bg-raised"
      >
        <BellIcon className="size-4" />
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="top-0 left-0 h-dvh max-h-none w-full max-w-none translate-x-0 translate-y-0 rounded-none border-r border-line lg:top-2 lg:left-[248px] lg:h-[calc(100dvh-1rem)] lg:w-[450px] lg:max-w-[calc(100vw-256px)] lg:rounded-xl">
          <DialogHeader className="px-5 pt-4 pb-3">
            <DialogTitle className="flex items-center gap-2 text-sm">
              <BellIcon className="size-4 text-muted-foreground" />
              Notifications<Badge variant="outline">{env}</Badge>
            </DialogTitle>
          </DialogHeader>
          <InboxBody key={env} env={env} onOpen={() => setOpen(false)} />
        </DialogContent>
      </Dialog>
    </>
  )
}
