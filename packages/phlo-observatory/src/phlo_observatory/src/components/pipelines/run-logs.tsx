/** Searchable, paged run diagnostics and captured Dagster process output. */
import * as React from 'react'
import {
  QueryClient,
  infiniteQueryOptions,
  useInfiniteQuery,
} from '@tanstack/react-query'
import { useVirtualizer } from '@tanstack/react-virtual'
import type { InfiniteData, QueryFunctionContext } from '@tanstack/react-query'
import type { getPipelineJob } from '@/lib/data/api/pipelines'
import { Button } from '@/components/ui/button'

type RunEvents = NonNullable<
  Awaited<ReturnType<typeof getPipelineJob>>['events']
>
type RunEvent = RunEvents['items'][number]
type RunError = NonNullable<RunEvent['error']>
type CapturedLog = NonNullable<RunEvents['captured_logs']>[number]
type SortKey =
  'time-newest' | 'time-oldest' | 'level' | 'event' | 'step' | 'message'

function errorText(error: RunError): string {
  return [
    error.class_name,
    error.message,
    ...error.stack,
    ...error.causes.map(errorText),
  ]
    .filter(Boolean)
    .join('\n')
}

export function formatRunLogTimestamp(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('en-GB', {
        year: 'numeric',
        month: 'short',
        day: '2-digit',
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        fractionalSecondDigits: 3,
        hourCycle: 'h23',
        timeZone: 'UTC',
        timeZoneName: 'short',
      }).format(date)
}

export function formatCompactRunLogTimestamp(value: string) {
  const date = new Date(value)
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat('en-GB', {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
        fractionalSecondDigits: 3,
        hourCycle: 'h23',
        timeZone: 'UTC',
      }).format(date)
}

function ErrorDetails({
  error,
  isCause = false,
}: {
  error: RunError
  isCause?: boolean
}) {
  return (
    <div
      className={
        isCause ? 'mt-3' : 'mt-3 rounded border border-line bg-background p-3'
      }
    >
      {isCause ? (
        <p className="m-0 break-words font-mono text-xs font-medium">
          Caused by {error.class_name ?? 'error'}: {error.message}
        </p>
      ) : (
        <p className="m-0 break-words font-mono text-sm font-medium text-bad-text">
          {error.class_name ?? 'Run error'}: {error.message}
        </p>
      )}
      {error.stack.length ? (
        <pre
          role="region"
          aria-label={`${error.class_name ?? 'Run'} stack trace`}
          tabIndex={0}
          className="mb-0 mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-words font-mono text-xs focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          {error.stack.join('')}
        </pre>
      ) : null}
      {error.causes.map((cause, index) => (
        <ErrorDetails
          key={`${cause.class_name}:${index}`}
          error={cause}
          isCause
        />
      ))}
    </div>
  )
}

function CapturedOutput({ capture }: { capture: CapturedLog }) {
  const label = capture.step_keys.join(', ') || capture.file_key
  if (!capture.available)
    return (
      <p className="m-0 text-sm text-muted-foreground">
        Captured stdout/stderr for {label} is unavailable from this Dagster
        provider.
      </p>
    )
  return (
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      {(['stdout', 'stderr'] as const).map((stream) => {
        const content = capture[stream]
        return content === null ? null : (
          <div key={stream} className="min-w-0">
            <h4 className="mb-1 mt-0 text-xs font-medium">{stream}</h4>
            <pre
              role="region"
              aria-label={`${label} ${stream}`}
              tabIndex={0}
              className="m-0 max-h-64 min-w-0 overflow-auto rounded bg-background p-3 font-mono text-xs whitespace-pre-wrap break-words focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
            >
              {content}
            </pre>
          </div>
        )
      })}
      {capture.truncated ? (
        <p className="m-0 text-xs text-muted-foreground md:col-span-2">
          Captured output was truncated at 256 KiB per stream.
        </p>
      ) : null}
    </div>
  )
}

export function runLogVirtualizerOptions(
  items: Array<RunEvent>,
  rows: Array<RunEvent>,
) {
  const sourcePositions = new Map(items.map((item, index) => [item, index]))
  return {
    count: rows.length,
    // Source positions stay stable when appended pages change the visible sort order.
    getItemKey: (index: number) => sourcePositions.get(rows[index]) ?? index,
    estimateSize: () => 33,
    overscan: 8,
  }
}

function RunLogTable({
  items,
  rows,
  canLoadMore,
  loading,
  paused,
  loadMore,
}: {
  items: Array<RunEvent>
  rows: Array<RunEvent>
  canLoadMore: boolean
  loading: boolean
  paused: boolean
  loadMore: () => Promise<unknown>
}) {
  const [expanded, setExpanded] = React.useState<number | null>(null)
  const scrollRef = React.useRef<HTMLDivElement>(null)
  const virtualOptions = React.useMemo(
    () => runLogVirtualizerOptions(items, rows),
    [items, rows],
  )
  const virtualizer = useVirtualizer({
    ...virtualOptions,
    getScrollElement: () => scrollRef.current,
  })
  const visible = virtualizer.getVirtualItems()
  const lastVisibleIndex = visible.at(-1)?.index
  React.useEffect(() => {
    if (
      canLoadMore &&
      !loading &&
      !paused &&
      (rows.length === 0 ||
        (lastVisibleIndex !== undefined && lastVisibleIndex >= rows.length - 1))
    )
      void loadMore()
  }, [canLoadMore, loading, paused, rows.length, lastVisibleIndex, loadMore])
  const paddingTop = visible[0]?.start ?? 0
  const paddingBottom = visible.length
    ? virtualizer.getTotalSize() - (visible.at(-1)?.end ?? 0)
    : 0
  return (
    <div
      ref={scrollRef}
      role="region"
      aria-label="Run log table; scroll to load more events or horizontally to view all columns"
      tabIndex={0}
      className="max-h-[min(60vh,640px)] min-w-0 overflow-auto rounded-md border border-line focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
    >
      <table className="w-full min-w-[960px] table-fixed text-left text-xs">
        <colgroup>
          <col className="w-32" />
          <col className="w-20" />
          <col className="w-44" />
          <col className="w-48" />
          <col />
        </colgroup>
        <thead className="sticky top-0 bg-sunken text-muted-foreground">
          <tr>
            <th className="px-3 py-2 font-medium">Time (UTC)</th>
            <th className="px-3 py-2 font-medium">Level</th>
            <th className="px-3 py-2 font-medium">Event</th>
            <th className="px-3 py-2 font-medium">Step</th>
            <th className="px-3 py-2 font-medium">Message</th>
          </tr>
        </thead>
        {paddingTop > 0 ? (
          <tbody aria-hidden="true">
            <tr>
              <td colSpan={5} style={{ height: paddingTop, padding: 0 }} />
            </tr>
          </tbody>
        ) : null}
        {visible.map((virtualRow) => {
          const index = virtualRow.index
          const event = rows[index]
          const eventKey = virtualOptions.getItemKey(index)
          const isExpanded = expanded === eventKey
          const fullTimestamp = formatRunLogTimestamp(event.timestamp)
          return (
            <tbody
              key={virtualRow.key}
              data-index={index}
              ref={virtualizer.measureElement}
              className="border-b border-line"
            >
              <tr className="align-top hover:bg-sunken/70">
                <td className="whitespace-nowrap px-3 py-2 font-mono">
                  <time dateTime={event.timestamp} title={fullTimestamp}>
                    <span className="sr-only">{fullTimestamp}</span>
                    <span aria-hidden="true">
                      {formatCompactRunLogTimestamp(event.timestamp)}
                    </span>
                  </time>
                </td>
                <td className="px-3 py-2">
                  <span className="rounded bg-sunken px-1.5 py-0.5">
                    {event.level ?? '—'}
                  </span>
                </td>
                <td
                  className="truncate px-3 py-2 font-mono"
                  title={event.event_type}
                >
                  {event.event_type}
                </td>
                <td
                  className="truncate px-3 py-2 font-mono"
                  title={event.step_key ?? undefined}
                >
                  {event.step_key ?? '—'}
                </td>
                <td className="min-w-0 px-3 py-2">
                  <button
                    type="button"
                    aria-expanded={isExpanded}
                    aria-controls={`run-log-detail-${index}`}
                    className="block w-full cursor-pointer rounded-sm text-left text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
                    onClick={() => setExpanded(isExpanded ? null : eventKey)}
                  >
                    <span className="block truncate text-xs">
                      {event.message}
                    </span>
                  </button>
                </td>
              </tr>
              <tr id={`run-log-detail-${index}`} hidden={!isExpanded}>
                <td colSpan={5} className="bg-sunken px-4 py-3">
                  <p className="mt-0 mb-1 break-words text-xs text-muted-foreground">
                    {fullTimestamp} · {event.event_type}
                    {event.step_key ? ` · ${event.step_key}` : ''}
                  </p>
                  <p className="m-0 break-words text-sm">{event.message}</p>
                  {event.error ? <ErrorDetails error={event.error} /> : null}
                  {event.captured_file_key ? (
                    <p className="mb-0 mt-2 text-xs text-muted-foreground">
                      Captured output: {event.captured_file_key}
                    </p>
                  ) : null}
                </td>
              </tr>
            </tbody>
          )
        })}
        {paddingBottom > 0 ? (
          <tbody aria-hidden="true">
            <tr>
              <td colSpan={5} style={{ height: paddingBottom, padding: 0 }} />
            </tr>
          </tbody>
        ) : null}
        {!rows.length ? (
          <tbody>
            <tr>
              <td
                colSpan={5}
                className="px-3 py-8 text-center text-sm text-muted-foreground"
              >
                {items.length
                  ? 'No logs match these filters.'
                  : 'No log evidence returned.'}
              </td>
            </tr>
          </tbody>
        ) : null}
      </table>
    </div>
  )
}

function CapturedSection({
  captures,
  unavailable,
}: {
  captures: Array<CapturedLog>
  unavailable: boolean
}) {
  if (!captures.length)
    return unavailable ? (
      <p className="m-0 text-sm text-muted-foreground">
        Dagster reported captured logs, but its provider could not return their
        contents.
      </p>
    ) : null
  return (
    <details className="rounded-md border border-line px-3 py-2">
      <summary className="cursor-pointer text-sm font-medium focus-visible:rounded-sm focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
        Captured stdout/stderr ({captures.length})
      </summary>
      <div className="mt-3 flex flex-col gap-3">
        {captures.map((capture) => (
          <div
            key={capture.file_key}
            className="min-w-0 rounded border border-line-soft p-3"
          >
            <h3 className="m-0 text-xs font-medium">
              {capture.step_keys.join(', ') || capture.file_key}
            </h3>
            <CapturedOutput capture={capture} />
          </div>
        ))}
      </div>
    </details>
  )
}

function eventText(event: RunEvent) {
  return `${event.timestamp} ${event.level ?? ''} ${event.event_type} ${event.step_key ?? ''} ${event.message} ${event.error ? errorText(event.error) : ''}`
}

function eventTime(event: RunEvent) {
  const time = Date.parse(event.timestamp)
  return Number.isNaN(time) ? 0 : time
}

function compareEvents(a: RunEvent, b: RunEvent, sort: SortKey) {
  switch (sort) {
    case 'time-newest':
      return eventTime(b) - eventTime(a)
    case 'time-oldest':
      return eventTime(a) - eventTime(b)
    case 'level':
      return (a.level ?? '').localeCompare(b.level ?? '')
    case 'event':
      return a.event_type.localeCompare(b.event_type)
    case 'step':
      return (a.step_key ?? '').localeCompare(b.step_key ?? '')
    case 'message':
      return a.message.localeCompare(b.message)
  }
}

function uniqueValues(values: Array<string | null>): Array<string> {
  return [
    ...new Set(values.filter((value): value is string => Boolean(value))),
  ].sort()
}

export function visibleRunLogRows(
  items: Array<RunEvent>,
  filters: {
    level: string
    eventType: string
    step: string
    search: string
    sort: SortKey
  },
) {
  return items
    .filter(
      (item) =>
        (filters.level === 'all' || item.level === filters.level) &&
        (filters.eventType === 'all' ||
          item.event_type === filters.eventType) &&
        (filters.step === 'all' || item.step_key === filters.step) &&
        eventText(item).toLowerCase().includes(filters.search),
    )
    .toSorted((a, b) => compareEvents(a, b, filters.sort))
}

export function mergeRunLogPages(pages: Array<RunEvents>) {
  const last = pages.at(-1)
  if (!last) return null
  const captures = new Map(
    pages
      .flatMap((page) => page.captured_logs ?? [])
      .map((capture) => [capture.file_key, capture]),
  )
  return {
    ...last,
    items: pages.flatMap((page) => page.items),
    captured_logs: [...captures.values()],
    captured_logs_available: pages.findLast(
      (page) => page.captured_logs_available !== undefined,
    )?.captured_logs_available,
  }
}

export function runLogQueryOptions({
  runId,
  initialEvents,
  loadPage,
  client,
}: {
  runId: string
  initialEvents: RunEvents | null
  loadPage: (cursor: string, signal: AbortSignal) => Promise<RunEvents>
  client: QueryClient
}) {
  const queryKey = ['run-logs', initialEvents?.env, runId]
  return infiniteQueryOptions({
    queryKey,
    enabled: initialEvents !== null,
    initialPageParam: null,
    initialData: initialEvents
      ? { pages: [initialEvents], pageParams: [null] }
      : undefined,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    queryFn: async ({
      pageParam,
      signal,
    }: QueryFunctionContext<typeof queryKey, string | null>) => {
      if (pageParam === null) {
        if (!initialEvents) throw new Error('Run log evidence is unavailable.')
        return initialEvents
      }
      const page = await loadPage(pageParam, signal)
      signal.throwIfAborted()
      if (page.env !== initialEvents?.env || page.run_id !== runId)
        throw new Error('The next log page belongs to a different run.')
      const prior =
        client.getQueryData<InfiniteData<RunEvents, string | null>>(queryKey)
      if (page.truncated && page.items.length === 0)
        throw new Error(
          'Dagster returned an empty continuation page. Retry to load more logs.',
        )
      if (
        page.truncated &&
        (!page.next_cursor ||
          page.next_cursor === pageParam ||
          prior?.pageParams.includes(page.next_cursor))
      )
        throw new Error('The log cursor did not advance.')
      return page
    },
    getNextPageParam: (page) => (page.truncated ? page.next_cursor : null),
  })
}

function useRunLogPages(
  options: Omit<Parameters<typeof runLogQueryOptions>[0], 'client'>,
) {
  // A loader refresh gets a new cache, so logs never survive a principal or observation change.
  const client = React.useMemo(
    () => new QueryClient(),
    [options.initialEvents, options.runId],
  )
  React.useEffect(
    () => () => {
      void client.cancelQueries()
      client.clear()
    },
    [client],
  )
  const query = useInfiniteQuery(
    runLogQueryOptions({ ...options, client }),
    client,
  )
  const events = React.useMemo(
    () => mergeRunLogPages(query.data?.pages ?? []),
    [query.data],
  )
  const loadMore = React.useCallback(
    () => query.fetchNextPage({ cancelRefetch: false }),
    [query.fetchNextPage],
  )
  return {
    events,
    items: events?.items ?? [],
    loading: query.isFetchingNextPage,
    error: query.error?.message ?? null,
    loadMore,
    canLoadMore: query.hasNextPage,
  }
}

function downloadLogs(
  runId: string,
  rows: Array<RunEvent>,
  captures: Array<CapturedLog>,
) {
  const body = [
    ...rows.map((event) =>
      [
        `${event.timestamp} ${event.level ?? ''} ${event.event_type} ${event.step_key ?? ''}`,
        event.message,
        event.error ? errorText(event.error) : '',
      ]
        .filter(Boolean)
        .join('\n'),
    ),
    ...captures.map(
      (capture) =>
        `Captured output ${capture.step_keys.join(', ') || capture.file_key}\nstdout:\n${capture.stdout ?? '[unavailable]'}\nstderr:\n${capture.stderr ?? '[unavailable]'}`,
    ),
  ].join('\n\n')
  const url = URL.createObjectURL(new Blob([body], { type: 'text/plain' }))
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = `${runId}-logs.txt`
  anchor.click()
  URL.revokeObjectURL(url)
}

export function RunLogsPanel({
  runId,
  events: initialEvents,
  loadPage,
}: {
  runId: string
  events: RunEvents | null
  loadPage: (cursor: string, signal: AbortSignal) => Promise<RunEvents>
}) {
  const {
    events,
    items,
    loading,
    error: loadError,
    loadMore,
    canLoadMore,
  } = useRunLogPages({ runId, initialEvents, loadPage })
  const [searchText, setSearchText] = React.useState('')
  const [level, setLevel] = React.useState('all')
  const [eventType, setEventType] = React.useState('all')
  const [step, setStep] = React.useState('all')
  const [sort, setSort] = React.useState<SortKey>('time-oldest')
  React.useEffect(() => {
    setSearchText('')
    setLevel('all')
    setEventType('all')
    setStep('all')
    setSort('time-oldest')
  }, [runId])
  const search = searchText.trim().toLowerCase()
  const levels = uniqueValues(items.map((item) => item.level))
  const eventTypes = uniqueValues(items.map((item) => item.event_type))
  const steps = uniqueValues(items.map((item) => item.step_key))
  const rows = React.useMemo(
    () => visibleRunLogRows(items, { level, eventType, step, search, sort }),
    [items, level, eventType, step, search, sort],
  )
  const captures = (events?.captured_logs ?? []).filter((capture) => {
    const matchesStep =
      step === 'all' ||
      capture.step_keys.includes(step) ||
      capture.file_key === step
    const matchesSearch =
      `${capture.step_keys.join(' ')} ${capture.file_key} ${capture.stdout ?? ''} ${capture.stderr ?? ''}`
        .toLowerCase()
        .includes(search)
    return matchesStep && matchesSearch
  })
  if (!events)
    return (
      <section
        aria-labelledby="run-logs-heading"
        className="flex min-w-0 flex-col gap-3 border-b border-line px-4 py-5 lg:px-6"
      >
        <h2 id="run-logs-heading" className="m-0 text-sm font-medium">
          Logs
        </h2>
        <p role="alert" className="m-0 text-sm text-muted-foreground">
          Run logs are unavailable. Use Refresh to try again.
        </p>
      </section>
    )
  return (
    <section
      aria-labelledby="run-logs-heading"
      className="flex min-w-0 flex-col gap-3 border-b border-line px-4 py-5 lg:px-6"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h2 id="run-logs-heading" className="m-0 text-sm font-medium">
          Logs
        </h2>
        <div className="flex flex-wrap items-center justify-end gap-3">
          <p className="m-0 text-xs text-muted-foreground" aria-live="polite">
            {rows.length === items.length
              ? `${items.length} events loaded`
              : `${rows.length} of ${items.length} loaded events`}
          </p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => downloadLogs(runId, rows, captures)}
          >
            Download filtered logs
          </Button>
        </div>
      </div>
      <div className="grid min-w-0 grid-cols-2 gap-2 sm:grid-cols-2 xl:grid-cols-6">
        <input
          aria-label="Search run logs"
          className="col-span-2 min-w-0 rounded-md border border-line bg-background px-3 py-2 text-sm xl:col-span-2"
          placeholder="Search logs, steps, messages and errors"
          value={searchText}
          onChange={(event) => setSearchText(event.currentTarget.value)}
        />
        <label className="flex min-w-0 items-center gap-2 text-xs">
          Level
          <select
            aria-label="Filter run logs by level"
            className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-2"
            value={level}
            onChange={(event) => setLevel(event.currentTarget.value)}
          >
            <option value="all">All levels</option>
            {levels.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 items-center gap-2 text-xs">
          Event
          <select
            aria-label="Filter run logs by event"
            className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-2"
            value={eventType}
            onChange={(event) => setEventType(event.currentTarget.value)}
          >
            <option value="all">All events</option>
            {eventTypes.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 items-center gap-2 text-xs">
          Step
          <select
            aria-label="Filter run logs by step"
            className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-2"
            value={step}
            onChange={(event) => setStep(event.currentTarget.value)}
          >
            <option value="all">All steps</option>
            {steps.map((value) => (
              <option key={value}>{value}</option>
            ))}
          </select>
        </label>
        <label className="flex min-w-0 items-center gap-2 text-xs">
          Sort
          <select
            aria-label="Sort run logs"
            className="min-w-0 flex-1 rounded-md border border-line bg-background px-2 py-2"
            value={sort}
            onChange={(event) => {
              const value = event.currentTarget.value
              if (
                value === 'time-newest' ||
                value === 'time-oldest' ||
                value === 'level' ||
                value === 'event' ||
                value === 'step' ||
                value === 'message'
              )
                setSort(value)
            }}
          >
            <option value="time-newest">Newest first</option>
            <option value="time-oldest">Oldest first</option>
            <option value="level">Level</option>
            <option value="event">Event</option>
            <option value="step">Step</option>
            <option value="message">Message</option>
          </select>
        </label>
      </div>
      {events?.truncated && !canLoadMore ? (
        <p className="m-0 text-xs text-muted-foreground">
          Dagster returned partial logs without a continuation cursor. Refresh
          to try again.
        </p>
      ) : null}
      <p className="m-0 text-xs text-muted-foreground sm:hidden">
        Scroll the log table horizontally to view every column.
      </p>
      <RunLogTable
        key={JSON.stringify([search, level, eventType, step, sort])}
        items={items}
        rows={rows}
        canLoadMore={canLoadMore}
        loading={loading}
        paused={Boolean(loadError)}
        loadMore={loadMore}
      />
      <CapturedSection
        captures={captures}
        unavailable={events?.captured_logs_available === false}
      />
      {loadError ? (
        <div
          role="alert"
          className="flex flex-wrap items-center gap-2 text-sm text-danger"
        >
          <span>{loadError}</span>
          <Button
            variant="outline"
            disabled={loading}
            onClick={() => void loadMore()}
          >
            Retry
          </Button>
        </div>
      ) : null}
      {canLoadMore ? (
        <p
          role="status"
          className="m-0 text-xs text-muted-foreground"
          aria-live="polite"
        >
          {loading ? 'Loading more logs…' : 'Scroll for more logs'}
        </p>
      ) : null}
    </section>
  )
}
