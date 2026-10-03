/** Tests honest event search, facets, and deterministic log ordering. */
import { expect, it, vi } from 'vitest'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { InfiniteQueryObserver, QueryClient } from '@tanstack/react-query'
import { Virtualizer } from '@tanstack/react-virtual'
import {
  RunLogsPanel,
  formatCompactRunLogTimestamp,
  formatRunLogTimestamp,
  mergeRunLogPages,
  runLogQueryOptions,
  runLogVirtualizerOptions,
  visibleRunLogRows,
} from './run-logs'
import type { getPipelineJob } from '@/lib/data/api/pipelines'

type Events = NonNullable<Awaited<ReturnType<typeof getPipelineJob>>['events']>
type Event = Events['items'][number]

it('reports unavailable initial log evidence instead of presenting an empty run', () => {
  const html = renderToStaticMarkup(
    createElement(RunLogsPanel, {
      runId: 'unavailable-run',
      events: null,
      loadPage: () =>
        Promise.reject(new Error('Must not fetch without initial evidence')),
    }),
  )
  expect(html).toContain('Run logs are unavailable')
  expect(html).toContain('role="alert"')
  expect(html).not.toContain('0 events loaded')
  expect(html).not.toContain('Download filtered logs')
})

it('keeps a verified empty log page distinct from unavailable evidence', () => {
  const empty: Events = {
    env: 'prod',
    run_id: 'empty-run',
    items: [],
    truncated: false,
    next_cursor: null,
  }
  const html = renderToStaticMarkup(
    createElement(RunLogsPanel, {
      runId: 'empty-run',
      events: empty,
      loadPage: () => Promise.resolve(empty),
    }),
  )
  expect(html).toContain('0 events loaded')
  expect(html).not.toContain('Run logs are unavailable')
  expect(html).not.toContain('role="alert"')
})

function event(
  values: Partial<Event> & Pick<Event, 'timestamp' | 'event_type' | 'message'>,
): Event {
  return {
    timestamp: values.timestamp,
    event_type: values.event_type,
    message: values.message,
    level: values.level ?? 'INFO',
    step_key: values.step_key ?? null,
    error: values.error ?? null,
    captured_file_key: values.captured_file_key ?? null,
    captured_step_keys: values.captured_step_keys ?? [],
  }
}

const rows = [
  event({
    timestamp: '2026-10-03T07:37:20.114Z',
    level: 'INFO',
    event_type: 'STEP_START',
    step_key: 'fleet_daily_summary',
    message: 'Started daily summary',
  }),
  event({
    timestamp: '2026-10-03T07:37:24.952Z',
    level: 'ERROR',
    event_type: 'STEP_FAILURE',
    step_key: 'fleet_daily_summary',
    message: 'Step failed',
    error: {
      class_name: 'DagsterInvariantViolationError',
      message: 'Cannot access partition_key',
      stack: ['stack line'],
      causes: [],
    },
  }),
  event({
    timestamp: '2026-10-03T07:37:30.556Z',
    level: 'WARNING',
    event_type: 'RESOURCE_INIT_FAILURE',
    step_key: 'asset_check',
    message: 'Resource unavailable',
  }),
]

it('combines text search with level, event, and step facets', () => {
  expect(
    visibleRunLogRows(rows, {
      level: 'ERROR',
      eventType: 'STEP_FAILURE',
      step: 'fleet_daily_summary',
      search: 'partition_key',
      sort: 'time-newest',
    }).map((row) => row.event_type),
  ).toEqual(['STEP_FAILURE'])
  expect(
    visibleRunLogRows(rows, {
      level: 'ERROR',
      eventType: 'all',
      step: 'asset_check',
      search: '',
      sort: 'time-newest',
    }),
  ).toEqual([])
})

it('sorts by each supported column and time direction', () => {
  const select = (sort: Parameters<typeof visibleRunLogRows>[1]['sort']) =>
    visibleRunLogRows(rows, {
      level: 'all',
      eventType: 'all',
      step: 'all',
      search: '',
      sort,
    })
  expect(select('time-newest').map((row) => row.event_type)).toEqual([
    'RESOURCE_INIT_FAILURE',
    'STEP_FAILURE',
    'STEP_START',
  ])
  expect(select('time-oldest').map((row) => row.event_type)).toEqual([
    'STEP_START',
    'STEP_FAILURE',
    'RESOURCE_INIT_FAILURE',
  ])
  expect(select('level').map((row) => row.level)).toEqual([
    'ERROR',
    'INFO',
    'WARNING',
  ])
  expect(select('message').map((row) => row.message)).toEqual([
    'Resource unavailable',
    'Started daily summary',
    'Step failed',
  ])
})

it('retains all paged events and preserves capture availability across pages', () => {
  const item = rows[0]
  const page = (items: Array<Event>, truncated: boolean): Events => ({
    env: 'prod',
    run_id: 'run-1',
    truncated,
    next_cursor: truncated ? 'next' : null,
    items,
    captured_logs: [],
    captured_logs_available: undefined,
  })
  const captures: Events['captured_logs'] = [
    {
      file_key: 'stdout-one',
      step_keys: ['fleet_daily_summary'],
      stdout: 'first output',
      stderr: null,
      available: true,
      truncated: false,
    },
  ]
  const current = {
    ...page(
      Array.from({ length: 450 }, () => item),
      true,
    ),
    captured_logs: captures,
    captured_logs_available: true,
  }
  const next = {
    ...page(
      Array.from({ length: 100 }, () => item),
      true,
    ),
    captured_logs: captures,
  }
  const combined = mergeRunLogPages([current, next])
  expect(combined?.items).toHaveLength(550)
  expect(combined?.truncated).toBe(true)
  expect(combined?.captured_logs).toHaveLength(1)
  expect(combined?.captured_logs_available).toBe(true)
})

it('formats run timestamps deterministically in UTC with milliseconds', () => {
  const priorTimezone = process.env.TZ
  try {
    process.env.TZ = 'America/Los_Angeles'
    expect(formatRunLogTimestamp('2026-10-03T07:37:24.952Z')).toBe(
      '03 Oct 2026, 07:37:24.952 UTC',
    )
    expect(formatRunLogTimestamp('invalid timestamp')).toBe('invalid timestamp')
  } finally {
    if (priorTimezone === undefined) delete process.env.TZ
    else process.env.TZ = priorTimezone
  }
})

it('formats compact run timestamps with UTC millisecond precision', () => {
  expect(formatCompactRunLogTimestamp('2026-10-03T07:37:24.952Z')).toBe(
    '07:37:24.952',
  )
  expect(formatCompactRunLogTimestamp('invalid timestamp')).toBe(
    'invalid timestamp',
  )
})

it('keeps expanded measurements on the same event when a page changes sorted indices', () => {
  const filters = {
    level: 'all',
    eventType: 'all',
    step: 'all',
    search: '',
    sort: 'time-newest',
  } satisfies Parameters<typeof visibleRunLogRows>[1]
  const items = rows.slice(0, 2)
  const options = {
    ...runLogVirtualizerOptions(items, visibleRunLogRows(items, filters)),
    getScrollElement: () => null,
    observeElementRect: () => {},
    observeElementOffset: () => {},
    scrollToFn: () => {},
    initialRect: { width: 960, height: 640 },
  }
  const virtualizer = new Virtualizer<HTMLDivElement, HTMLTableSectionElement>(
    options,
  )
  expect(virtualizer.getVirtualItems().map((item) => item.key)).toEqual([1, 0])
  virtualizer.resizeItem(0, 157)
  expect(virtualizer.getTotalSize()).toBe(190)
  virtualizer.setOptions({
    ...options,
    ...runLogVirtualizerOptions(rows, visibleRunLogRows(rows, filters)),
  })
  expect(
    virtualizer
      .getVirtualItems()
      .map(({ key, size, start }) => ({ key, size, start })),
  ).toEqual([
    { key: 2, size: 33, start: 0 },
    { key: 1, size: 157, start: 33 },
    { key: 0, size: 33, start: 190 },
  ])
})

it('rejects advancing empty continuation pages without growing the cache and retries the original cursor', async () => {
  const client = new QueryClient()
  const initial: Events = {
    env: 'prod',
    run_id: 'run-1',
    items: [rows[0]],
    truncated: true,
    next_cursor: 'page-2',
  }
  const loadPage = vi.fn((_cursor: string, _signal: AbortSignal) =>
    Promise.resolve({
      ...initial,
      items: loadPage.mock.calls.length < 3 ? [] : [rows[1]],
      next_cursor: `page-${loadPage.mock.calls.length + 2}`,
    }),
  )
  const observer = new InfiniteQueryObserver(
    client,
    runLogQueryOptions({
      client,
      initialEvents: initial,
      runId: 'run-1',
      loadPage,
    }),
  )
  const unsubscribe = observer.subscribe(() => {})
  try {
    for (let attempt = 0; attempt < 2; attempt++) {
      const result = await observer.fetchNextPage({ cancelRefetch: false })
      expect(result.error?.message).toContain('empty continuation page')
      expect(result.data?.pages).toEqual([initial])
    }
    const retried = await observer.fetchNextPage({ cancelRefetch: false })
    expect(retried.error).toBeNull()
    expect(retried.data?.pages.flatMap((page) => page.items)).toEqual(
      rows.slice(0, 2),
    )
    expect(loadPage.mock.calls.map(([cursor]) => cursor)).toEqual([
      'page-2',
      'page-2',
      'page-2',
    ])
  } finally {
    unsubscribe()
    client.clear()
  }
})

it('uses TanStack paging to retain evidence after failure and retry the same cursor', async () => {
  const client = new QueryClient()
  const initial: Events = {
    env: 'prod',
    run_id: 'run-1',
    items: [rows[0]],
    truncated: true,
    next_cursor: 'page-2',
  }
  const loadPage = vi.fn((_cursor: string, _signal: AbortSignal) => {
    if (loadPage.mock.calls.length === 1)
      return Promise.reject(new Error('Provider timeout'))
    return Promise.resolve({
      ...initial,
      items: rows.slice(1),
      truncated: false,
      next_cursor: null,
    })
  })
  const observer = new InfiniteQueryObserver(
    client,
    runLogQueryOptions({
      client,
      initialEvents: initial,
      runId: 'run-1',
      loadPage,
    }),
  )
  const unsubscribe = observer.subscribe(() => {})
  try {
    const failed = await observer.fetchNextPage({ cancelRefetch: false })
    expect(failed.error?.message).toBe('Provider timeout')
    expect(failed.data?.pages).toEqual([initial])
    expect(loadPage).toHaveBeenCalledTimes(1)
    const retried = await observer.fetchNextPage({ cancelRefetch: false })
    expect(retried.error).toBeNull()
    expect(
      retried.data?.pages
        .flatMap((page) => page.items)
        .map((item) => item.event_type),
    ).toEqual(['STEP_START', 'STEP_FAILURE', 'RESOURCE_INIT_FAILURE'])
    expect(retried.hasNextPage).toBe(false)
    expect(loadPage.mock.calls.map((call) => call[0])).toEqual([
      'page-2',
      'page-2',
    ])
  } finally {
    unsubscribe()
    client.clear()
  }
})

it.each([
  { env: 'staging' as const },
  { run_id: 'foreign-run' },
  { next_cursor: 'page-2' },
  { next_cursor: null },
])(
  'rejects untrusted or non-advancing log pages without replacing evidence: %j',
  async (invalid) => {
    const client = new QueryClient()
    const initial: Events = {
      env: 'prod',
      run_id: 'run-1',
      items: [rows[0]],
      truncated: true,
      next_cursor: 'page-2',
    }
    const observer = new InfiniteQueryObserver(
      client,
      runLogQueryOptions({
        client,
        initialEvents: initial,
        runId: 'run-1',
        loadPage: () =>
          Promise.resolve({
            ...initial,
            items: [rows[1]],
            next_cursor: 'page-3',
            ...invalid,
          }),
      }),
    )
    const unsubscribe = observer.subscribe(() => {})
    try {
      const result = await observer.fetchNextPage({ cancelRefetch: false })
      expect(result.isFetchNextPageError).toBe(true)
      expect(result.data?.pages).toEqual([initial])
    } finally {
      unsubscribe()
      client.clear()
    }
  },
)

it('deduplicates overlapping next-page requests and aborts an obsolete observation', async () => {
  const client = new QueryClient()
  const initial: Events = {
    env: 'prod',
    run_id: 'run-1',
    items: [rows[0]],
    truncated: true,
    next_cursor: 'page-2',
  }
  let resolve: ((page: Events) => void) | undefined
  const pending = new Promise<Events>((finish) => {
    resolve = finish
  })
  const loadPage = vi.fn((_cursor: string, _signal: AbortSignal) => pending)
  const options = runLogQueryOptions({
    client,
    initialEvents: initial,
    runId: 'run-1',
    loadPage,
  })
  const observer = new InfiniteQueryObserver(client, options)
  const unsubscribe = observer.subscribe(() => {})
  try {
    const first = observer.fetchNextPage({ cancelRefetch: false })
    const second = observer.fetchNextPage({ cancelRefetch: false })
    expect(loadPage).toHaveBeenCalledTimes(1)
    await client.cancelQueries({ queryKey: options.queryKey })
    expect(loadPage.mock.calls[0][1].aborted).toBe(true)
    expect(resolve).toBeTypeOf('function')
    resolve?.({
      ...initial,
      items: [rows[1]],
      truncated: false,
      next_cursor: null,
    })
    await Promise.all([first, second])
    expect(observer.getCurrentResult().data?.pages).toEqual([initial])
  } finally {
    unsubscribe()
    client.clear()
  }
})

it('continues TanStack pagination beyond 500 events until the provider is exhausted', async () => {
  const client = new QueryClient()
  const initial: Events = {
    env: 'prod',
    run_id: 'run-1',
    items: Array.from({ length: 450 }, () => rows[0]),
    truncated: true,
    next_cursor: 'page-2',
  }
  const observer = new InfiniteQueryObserver(
    client,
    runLogQueryOptions({
      client,
      initialEvents: initial,
      runId: 'run-1',
      loadPage: (cursor) =>
        Promise.resolve({
          ...initial,
          items: Array.from({ length: 75 }, () => rows[1]),
          truncated: cursor === 'page-2',
          next_cursor: cursor === 'page-2' ? 'page-3' : null,
        }),
    }),
  )
  const unsubscribe = observer.subscribe(() => {})
  try {
    const result = await observer.fetchNextPage({ cancelRefetch: false })
    expect(result.hasNextPage).toBe(true)
    expect(mergeRunLogPages(result.data?.pages ?? [])?.items).toHaveLength(525)
    const exhausted = await observer.fetchNextPage({ cancelRefetch: false })
    expect(exhausted.hasNextPage).toBe(false)
    expect(mergeRunLogPages(exhausted.data?.pages ?? [])?.items).toHaveLength(
      600,
    )
    expect(mergeRunLogPages(exhausted.data?.pages ?? [])?.truncated).toBe(false)
  } finally {
    unsubscribe()
    client.clear()
  }
})

it('renders a bounded virtual window for thousands of loaded events', () => {
  const items = Array.from({ length: 5000 }, (_, index) =>
    event({
      timestamp: new Date(Date.UTC(2026, 9, 3) + index).toISOString(),
      event_type: 'STEP_OUTPUT',
      message: `Event ${index}`,
    }),
  )
  const ordered = visibleRunLogRows(items, {
    level: 'all',
    eventType: 'all',
    step: 'all',
    search: '',
    sort: 'time-newest',
  })
  const virtualizer = new Virtualizer<HTMLDivElement, HTMLTableSectionElement>({
    ...runLogVirtualizerOptions(items, ordered),
    getScrollElement: () => null,
    observeElementRect: () => {},
    observeElementOffset: () => {},
    scrollToFn: () => {},
    initialRect: { width: 960, height: 640 },
  })
  const visible = virtualizer.getVirtualItems()
  expect(visible.length).toBeLessThan(40)
  expect(visible[0].key).toBe(4999)
  expect(virtualizer.getTotalSize()).toBe(165000)
})
