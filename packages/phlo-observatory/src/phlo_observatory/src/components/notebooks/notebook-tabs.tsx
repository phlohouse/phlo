/** A strip of open notebooks. Switching keeps each one's unsaved edits and results. */
import * as React from 'react'
import { Link, useNavigate } from '@tanstack/react-router'
import { XIcon } from 'lucide-react'
import {
  dirtyPaths,
  dropDraft,
  readTabs,
  subscribeTabs,
  writeTabs,
} from '@/lib/notebook-tabs'
import { cn } from '@/lib/utils'

function fileName(path: string) {
  return (path.split('/').at(-1) ?? path).replace(/\.ipynb$/, '')
}

function useTabs(env: string) {
  const snapshot = React.useRef({
    key: '',
    tabs: [] as Array<string>,
    dirty: [] as Array<string>,
  })
  const read = React.useCallback(() => {
    const tabs = readTabs(env)
    const dirty = dirtyPaths(env)
    const key = `${tabs.join('\n')}\u0000${dirty.join('\n')}`
    if (key !== snapshot.current.key) snapshot.current = { key, tabs, dirty }
    return snapshot.current
  }, [env])
  const server = React.useRef({
    key: '',
    tabs: [] as Array<string>,
    dirty: [] as Array<string>,
  })
  return React.useSyncExternalStore(subscribeTabs, read, () => server.current)
}

export function NotebookTabs({
  env,
  active,
  known,
}: {
  env: string
  active: string
  /** Notebooks that still exist, so a deleted one drops out */
  known: ReadonlyArray<string>
}) {
  const { tabs, dirty } = useTabs(env)
  const navigate = useNavigate()
  const shown = tabs.filter((path) => path === active || known.includes(path))
  if (shown.length < 2) return null
  const close = (path: string) => {
    if (
      dirty.includes(path) &&
      !window.confirm(`Close ${fileName(path)} without saving your changes?`)
    )
      return
    dropDraft(env, path)
    const rest = shown.filter((item) => item !== path)
    writeTabs(env, rest)
    if (path === active && rest.length)
      void navigate({ to: '/notebooks', search: { env, path: rest.at(-1) } })
  }
  return (
    <nav
      aria-label="Open notebooks"
      className="flex shrink-0 items-end gap-0.5 overflow-x-auto border-b border-line px-2 pt-1.5 lg:pl-3"
    >
      {shown.map((path) => {
        const current = path === active
        return (
          <div
            key={path}
            className={cn(
              'group flex h-8 max-w-[220px] shrink-0 items-center rounded-t-md border border-b-0 text-[12.5px]',
              current
                ? 'border-line bg-card text-foreground'
                : 'border-transparent text-text-2 hover:bg-nav-hover',
            )}
          >
            <Link
              to="/notebooks"
              search={{ env, path }}
              aria-current={current ? 'page' : undefined}
              title={path}
              className="flex min-w-0 items-center gap-1.5 py-1 pr-1 pl-2.5 text-inherit no-underline"
            >
              <span className="truncate">{fileName(path)}</span>
              {dirty.includes(path) ? (
                <span
                  aria-label="Unsaved changes"
                  className="size-1.5 shrink-0 rounded-full bg-text-3"
                />
              ) : null}
            </Link>
            <button
              type="button"
              aria-label={`Close ${fileName(path)}`}
              onClick={() => close(path)}
              className="mr-1 inline-flex size-5 shrink-0 items-center justify-center rounded text-faint hover:bg-soft hover:text-foreground"
            >
              <XIcon className="size-3" />
            </button>
          </div>
        )
      })}
    </nav>
  )
}
