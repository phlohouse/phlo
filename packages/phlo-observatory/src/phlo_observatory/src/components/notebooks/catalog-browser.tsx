/** Database explorer: schemas, tables and views, and their columns, loaded as you expand them. */
import * as React from 'react'
import {
  ChevronDownIcon,
  ChevronRightIcon,
  CopyIcon,
  DatabaseIcon,
  EyeIcon,
  FolderIcon,
  Loader2Icon,
  PlayIcon,
  RefreshCwIcon,
  SearchIcon,
  TableIcon,
} from 'lucide-react'
import type { CatalogObject, CatalogObjects } from '@/lib/data/api/catalog'
import { getCatalogObjects } from '@/lib/data/api/catalog'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

type Level = {
  state: 'loading' | 'ready' | 'error'
  items: Array<CatalogObject>
  next: number | null
  message?: string
}
type Load = (
  key: string,
  schema?: string,
  table?: string,
  more?: boolean,
) => void

/** SQL identifier with quotes only when needed, so `SELECT` text stays readable. */
export function sqlIdentifier(name: string) {
  return /^[a-z_][a-z0-9_]*$/.test(name)
    ? name
    : `"${name.replace(/"/g, '""')}"`
}

export function qualifiedName(catalog: string, schema: string, table: string) {
  return [catalog, schema, table].map(sqlIdentifier).join('.')
}

const row =
  'group flex h-7 w-full items-center gap-1.5 rounded-md pr-1 text-left text-[13px] text-text-2 hover:bg-nav-hover hover:text-foreground'

function useCatalog(env: string) {
  const [levels, setLevels] = React.useState<Record<string, Level>>({})
  const [catalog, setCatalog] = React.useState<{
    name: string
    ref: string
  } | null>(null)
  const levelsRef = React.useRef(levels)
  levelsRef.current = levels
  const load: Load = React.useCallback(
    (key, schema, table, more = false) => {
      const current = levelsRef.current[key] as Level | undefined
      if (!more && current && current.state !== 'error') return
      const offset = more ? (current?.next ?? 0) : 0
      setLevels((all) => ({
        ...all,
        [key]: {
          items: more ? (all[key]?.items ?? []) : [],
          next: null,
          state: 'loading',
        },
      }))
      getCatalogObjects({ data: { env, schema, table, offset } }).then(
        (result) => {
          if (result.state === 'unavailable') {
            setLevels((all) => ({
              ...all,
              [key]: {
                items: [],
                next: null,
                state: 'error',
                message: result.message,
              },
            }))
            return
          }
          const page: CatalogObjects = result.page
          setCatalog({ name: page.catalog, ref: page.nessie_ref })
          setLevels((all) => ({
            ...all,
            [key]: {
              state: 'ready',
              items: [...(more ? (all[key]?.items ?? []) : []), ...page.items],
              next: page.next_offset,
            },
          }))
        },
        (error: unknown) =>
          setLevels((all) => ({
            ...all,
            [key]: {
              items: [],
              next: null,
              state: 'error',
              message:
                error instanceof Error
                  ? error.message
                  : 'The catalog could not be read.',
            },
          })),
      )
    },
    [env],
  )
  const reset = () => setLevels({})
  return { levels, catalog, load, reset }
}

function LevelStatus({
  level,
  depth,
  onMore,
}: {
  level?: Level
  depth: number
  onMore: () => void
}) {
  const pad = { paddingLeft: `${8 + depth * 14}px` }
  if (!level) return null
  if (level.state === 'error')
    return (
      <li role="alert" style={pad} className="py-1 pr-2 text-xs text-bad-text">
        {level.message}
      </li>
    )
  if (level.state === 'loading')
    return (
      <li
        style={pad}
        className="flex h-7 items-center gap-1.5 text-xs text-muted-foreground"
      >
        <Loader2Icon className="size-3 animate-spin" /> Loading
      </li>
    )
  if (level.items.length === 0)
    return (
      <li style={pad} className="py-1 text-xs text-muted-foreground">
        Empty
      </li>
    )
  if (level.next !== null)
    return (
      <li style={pad}>
        <button
          type="button"
          onClick={onMore}
          className="h-7 text-xs text-link hover:underline"
        >
          Show more
        </button>
      </li>
    )
  return null
}

function Columns({ level }: { level?: Level }) {
  return (
    <>
      {(level?.items ?? []).map((column) => (
        <li
          key={column.name}
          style={{ paddingLeft: '50px' }}
          className="flex h-6 items-center gap-2 pr-2 font-mono text-[12px]"
          title={`${column.name} ${column.data_type ?? ''}${column.nullable === false ? ' not null' : ''}`}
        >
          <span className="min-w-0 truncate text-text-2">{column.name}</span>
          <span className="ml-auto shrink-0 truncate text-[11px] text-faint">
            {column.data_type}
          </span>
        </li>
      ))}
    </>
  )
}

function TableRow({
  schema,
  table,
  catalog,
  open,
  toggle,
  level,
  load,
  onQuery,
}: {
  schema: string
  table: CatalogObject
  catalog: string
  open: boolean
  toggle: () => void
  level?: Level
  load: Load
  onQuery: (name: string, table: string) => void
}) {
  const key = `${schema}\u0000${table.name}`
  const Chevron = open ? ChevronDownIcon : ChevronRightIcon
  const Icon = table.kind === 'view' ? EyeIcon : TableIcon
  const name = qualifiedName(catalog, schema, table.name)
  return (
    <li>
      <div className={cn(row, 'pl-[36px]')}>
        <button
          type="button"
          aria-expanded={open}
          aria-label={`${table.kind === 'view' ? 'View' : 'Table'} ${schema}.${table.name}`}
          onClick={() => {
            toggle()
            if (!open) load(key, schema, table.name)
          }}
          className="flex min-w-0 flex-1 items-center gap-1.5 text-left outline-none focus-visible:outline-2 focus-visible:outline-ring"
        >
          <Chevron className="size-3.5 shrink-0 text-faint" />
          <Icon
            className={cn(
              'size-3.5 shrink-0',
              table.kind === 'view' ? 'text-teal' : 'text-faint',
            )}
          />
          <span className="min-w-0 truncate" title={table.name}>
            {table.name}
          </span>
          {table.kind === 'view' ? (
            <span className="shrink-0 text-[10.5px] text-faint">view</span>
          ) : null}
        </button>
        <span className="hidden shrink-0 group-focus-within:flex group-hover:flex pointer-coarse:flex">
          <button
            type="button"
            aria-label={`Copy ${name}`}
            title="Copy name"
            onClick={() => void navigator.clipboard.writeText(name)}
            className="inline-flex size-6 items-center justify-center rounded text-text-3 hover:bg-soft hover:text-foreground"
          >
            <CopyIcon className="size-3" />
          </button>
          <button
            type="button"
            aria-label={`Query ${schema}.${table.name} in a new cell`}
            title="Query in a new cell"
            onClick={() => onQuery(name, table.name)}
            className="inline-flex size-6 items-center justify-center rounded text-primary hover:bg-primary-soft"
          >
            <PlayIcon className="size-3 fill-current" />
          </button>
        </span>
      </div>
      {open ? (
        <ul>
          <Columns level={level} />
          <LevelStatus
            level={level}
            depth={3.5}
            onMore={() => load(key, schema, table.name, true)}
          />
        </ul>
      ) : null}
    </li>
  )
}

export function CatalogBrowser({
  env,
  onQuery,
}: {
  env: string
  /** Adds a SQL cell that reads this table */
  onQuery: (qualifiedName: string, table: string) => void
}) {
  const { levels, catalog, load, reset } = useCatalog(env)
  const [open, setOpen] = React.useState<Set<string>>(new Set())
  const [filter, setFilter] = React.useState('')
  const root = levels['']
  React.useEffect(() => {
    if (!root) load('')
  }, [load, root])
  const toggle = (key: string) =>
    setOpen((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  const needle = filter.trim().toLowerCase()
  const matches = (name: string) =>
    !needle || name.toLowerCase().includes(needle)

  return (
    <div className="flex flex-col gap-2.5">
      <div className="flex items-center gap-1.5">
        <div className="relative min-w-0 flex-1">
          <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-faint" />
          <Input
            aria-label="Filter schemas and tables"
            placeholder="Filter loaded objects"
            value={filter}
            onChange={(event) => setFilter(event.target.value)}
            className="h-8 pl-8"
          />
        </div>
        <Button
          variant="ghost"
          size="icon-sm"
          aria-label="Refresh catalog"
          onClick={() => {
            setOpen(new Set())
            reset()
          }}
        >
          <RefreshCwIcon />
        </Button>
      </div>
      <nav aria-label="Lakehouse catalog">
        <div className="flex items-center gap-1.5 px-2 pb-1 text-[13px] text-text-3">
          <DatabaseIcon className="size-3.5 shrink-0" />
          <span className="truncate font-mono text-[12.5px]">
            {catalog?.name ?? 'lakehouse'}
          </span>
          {catalog ? (
            <span className="ml-auto shrink-0 font-mono text-[11px] text-branch">
              {catalog.ref}
            </span>
          ) : null}
        </div>
        <ul>
          {(root?.items ?? [])
            .filter(
              (schema) =>
                matches(schema.name) ||
                (levels[schema.name]?.items ?? []).some((t) => matches(t.name)),
            )
            .map((schema) => {
              const expanded =
                open.has(schema.name) || (!!needle && !!levels[schema.name])
              const Chevron = expanded ? ChevronDownIcon : ChevronRightIcon
              const tables = levels[schema.name]
              return (
                <li key={schema.name}>
                  <button
                    type="button"
                    aria-expanded={expanded}
                    aria-label={`Schema ${schema.name}`}
                    onClick={() => {
                      toggle(schema.name)
                      if (!expanded) load(schema.name, schema.name)
                    }}
                    className={cn(row, 'pl-2')}
                  >
                    <Chevron className="size-3.5 shrink-0 text-faint" />
                    <FolderIcon className="size-3.5 shrink-0 text-faint" />
                    <span className="min-w-0 truncate">{schema.name}</span>
                    {tables?.state === 'ready' ? (
                      <span className="ml-auto shrink-0 pr-1 text-[11px] text-faint">
                        {tables.items.length}
                        {tables.next !== null ? '+' : ''}
                      </span>
                    ) : null}
                  </button>
                  {expanded ? (
                    <ul>
                      {(tables?.items ?? [])
                        .filter(
                          (table) =>
                            matches(table.name) || matches(schema.name),
                        )
                        .map((table) => {
                          const key = `${schema.name}\u0000${table.name}`
                          return (
                            <TableRow
                              key={table.name}
                              schema={schema.name}
                              table={table}
                              catalog={catalog?.name ?? ''}
                              open={open.has(key)}
                              toggle={() => toggle(key)}
                              level={levels[key]}
                              load={load}
                              onQuery={onQuery}
                            />
                          )
                        })}
                      <LevelStatus
                        level={tables}
                        depth={1.5}
                        onMore={() =>
                          load(schema.name, schema.name, undefined, true)
                        }
                      />
                    </ul>
                  ) : null}
                </li>
              )
            })}
          <LevelStatus
            level={root}
            depth={0}
            onMore={() => load('', undefined, undefined, true)}
          />
        </ul>
      </nav>
    </div>
  )
}
