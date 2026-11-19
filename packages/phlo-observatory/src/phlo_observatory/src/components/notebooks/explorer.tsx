/** Notebook file tree with search, folder toggles and a new-notebook field. */
import * as React from 'react'
import { Link } from '@tanstack/react-router'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  PlusIcon,
  SearchIcon,
} from 'lucide-react'
import type { TreeNode } from '@/lib/notebook'
import { notebookTree } from '@/lib/notebook'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

function Folder({
  node,
  depth,
  active,
  dirty,
  open,
  toggle,
  env,
}: {
  node: TreeNode
  depth: number
  active?: string
  dirty: boolean
  open: ReadonlySet<string>
  toggle: (path: string) => void
  env: string
}) {
  const pad = { paddingLeft: `${8 + depth * 14}px` }
  if (node.file) {
    const current = node.path === active
    return (
      <li>
        <Link
          to="/notebooks"
          search={{ env, path: node.path }}
          aria-current={current ? 'page' : undefined}
          title={node.path}
          style={pad}
          className={cn(
            'flex h-7 items-center gap-1.5 rounded-md pr-2 text-[13px] text-text-2 hover:bg-nav-hover hover:text-foreground',
            current && 'bg-nav-on text-foreground',
          )}
        >
          <span className="w-3.5 shrink-0" />
          <span className="min-w-0 flex-1 truncate">{node.name}</span>
          {current && dirty ? (
            <span
              className="font-mono text-[10.5px] text-warn"
              title="Unsaved changes"
            >
              M
            </span>
          ) : null}
        </Link>
      </li>
    )
  }
  const expanded = open.has(node.path)
  const Chevron = expanded ? ChevronDownIcon : ChevronRightIcon
  return (
    <li>
      <button
        type="button"
        aria-expanded={expanded}
        onClick={() => toggle(node.path)}
        style={pad}
        className="flex h-7 w-full items-center gap-1.5 rounded-md pr-2 text-left text-[13px] text-text-2 hover:bg-nav-hover hover:text-foreground"
      >
        <Chevron className="size-3.5 shrink-0 text-faint" />
        <span className="min-w-0 flex-1 truncate">{node.name}</span>
      </button>
      {expanded ? (
        <ul>
          {node.children.map((child) => (
            <Folder
              key={child.path}
              node={child}
              depth={depth + 1}
              active={active}
              dirty={dirty}
              open={open}
              toggle={toggle}
              env={env}
            />
          ))}
        </ul>
      ) : null}
    </li>
  )
}

export function NotebookExplorer({
  env,
  paths,
  active,
  dirty,
  truncated,
  reads,
  onNew,
}: {
  env: string
  paths: ReadonlyArray<string>
  active?: string
  dirty: boolean
  truncated: boolean
  /** Lakehouse tables the open notebook reads */
  reads: ReadonlyArray<string>
  /** Opens the new-notebook dialog */
  onNew: () => void
}) {
  const [query, setQuery] = React.useState('')
  const filtered = query.trim()
    ? paths.filter((path) =>
        path.toLowerCase().includes(query.trim().toLowerCase()),
      )
    : paths
  const tree = React.useMemo(() => notebookTree(filtered), [filtered])
  const [open, setOpen] = React.useState<Set<string>>(() => {
    const folders = new Set<string>()
    const parts = (active ?? '').split('/').slice(0, -1)
    parts.forEach((_, index) =>
      folders.add(parts.slice(0, index + 1).join('/')),
    )
    return folders
  })
  const effectiveOpen = query.trim()
    ? new Set(
        filtered.flatMap((path) => {
          const parts = path.split('/').slice(0, -1)
          return parts.map((_, index) => parts.slice(0, index + 1).join('/'))
        }),
      )
    : open
  const toggle = (path: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-1.5">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            aria-label="Find a notebook"
            placeholder="Find a notebook"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            className="h-8 pl-8"
          />
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="New notebook"
          onClick={onNew}
        >
          <PlusIcon />
        </Button>
      </div>
      <nav aria-label="Notebooks">
        <div className="px-2 pb-1 text-[13px] text-text-3">notebooks</div>
        {tree.length ? (
          <ul>
            {tree.map((node) => (
              <Folder
                key={node.path}
                node={node}
                depth={0}
                active={active}
                dirty={dirty}
                open={effectiveOpen}
                toggle={toggle}
                env={env}
              />
            ))}
          </ul>
        ) : (
          <p className="px-2 text-xs text-muted-foreground">
            {query ? 'No notebook matches.' : 'No notebooks yet.'}
          </p>
        )}
        {truncated ? (
          <p className="px-2 pt-1 text-xs text-muted-foreground">
            Showing the first 500 notebooks.
          </p>
        ) : null}
      </nav>
      {reads.length ? (
        <div className="border-t border-line-soft pt-2.5">
          <div className="px-2 pb-1.5 text-[11px] font-medium tracking-wide text-faint uppercase">
            Tables this notebook reads
          </div>
          <ul className="flex flex-col gap-1">
            {reads.map((table) => (
              <li
                key={table}
                className="flex items-center gap-2 px-2 font-mono text-[12px] text-text-2"
              >
                <span
                  aria-hidden
                  className="size-2 shrink-0 rounded-[2px] bg-gold"
                />
                <span className="truncate" title={table}>
                  {table}
                </span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
    </div>
  )
}
