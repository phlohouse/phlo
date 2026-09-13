/**
 * The console route — Observatory's only surface. `?focus=<kind>:<id>`
 * opens the inspector on an object; `?stream=1` opens the stream drawer;
 * `?in=<cluster>` expands a map unit. `?demo=N` renders a synthetic
 * N-asset lakehouse for scale testing. All shareable links, not pages.
 */
import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { Console } from '@/console/console'
import { parseFocus } from '@/console/store'

interface ConsoleSearch {
  demo?: number
  focus?: string
  in?: string
  stream?: boolean
}

export const Route = createFileRoute('/')({
  validateSearch: (search: Record<string, unknown>): ConsoleSearch => ({
    demo:
      Number(search.demo) > 0
        ? Math.min(Math.floor(Number(search.demo)), 5000)
        : undefined,
    focus:
      typeof search.focus === 'string' && parseFocus(search.focus)
        ? search.focus
        : undefined,
    in: typeof search.in === 'string' ? search.in : undefined,
    stream:
      search.stream === true ||
      search.stream === 'true' ||
      search.stream === '1' ||
      undefined,
  }),
  component: ConsoleRoute,
})

function ConsoleRoute() {
  const { demo, focus, in: expanded, stream } = Route.useSearch()
  const navigate = useNavigate()

  const patch = (next: Partial<ConsoleSearch>) =>
    navigate({
      replace: true,
      search: (previous: Record<string, unknown>) => ({
        ...previous,
        ...next,
      }),
      to: '/',
    })

  return (
    <Console
      demoCount={demo ?? null}
      expanded={expanded ?? null}
      focus={focus ? parseFocus(focus) : null}
      onExpand={(clusterId) => patch({ in: clusterId ?? undefined })}
      onFocus={(next) =>
        patch({ focus: next ? `${next.kind}:${next.id}` : undefined })
      }
      onStreamOpen={(open) => patch({ stream: open || undefined })}
      streamOpen={stream === true}
    />
  )
}
