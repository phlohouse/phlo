/**
 * The console route — Observatory's only surface. `?focus=<kind>:<id>`
 * opens the inspector on an object; `?stream=1` opens the stream drawer.
 * Both are shareable links, not pages.
 */
import { createFileRoute, useNavigate } from '@tanstack/react-router'

import { Console } from '@/console/console'
import { parseFocus } from '@/console/store'

interface ConsoleSearch {
  focus?: string
  stream?: boolean
}

export const Route = createFileRoute('/')({
  validateSearch: (search: Record<string, unknown>): ConsoleSearch => ({
    focus:
      typeof search.focus === 'string' && parseFocus(search.focus)
        ? search.focus
        : undefined,
    stream:
      search.stream === true ||
      search.stream === 'true' ||
      search.stream === '1' ||
      undefined,
  }),
  component: ConsoleRoute,
})

function ConsoleRoute() {
  const { focus, stream } = Route.useSearch()
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
      focus={focus ? parseFocus(focus) : null}
      onFocus={(next) =>
        patch({ focus: next ? `${next.kind}:${next.id}` : undefined })
      }
      onStreamOpen={(open) => patch({ stream: open || undefined })}
      streamOpen={stream === true}
    />
  )
}
