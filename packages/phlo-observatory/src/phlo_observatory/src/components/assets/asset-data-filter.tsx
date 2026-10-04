/** Asset-preview adaptation of sadmann7/tablecn's command filter menu. */
import * as React from 'react'
import { CheckIcon, ListFilterIcon, PlusIcon, XIcon } from 'lucide-react'
import type { AssetPreview, PreviewFilter } from '@/lib/data/api/assets'
import { previewFilterSchema } from '@/lib/data/api/assets'
import { Button } from '@/components/ui/button'
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { Input } from '@/components/ui/input'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/menu'
import { Select } from '@/components/ui/select'

type PreviewColumn = AssetPreview['columns'][number]
type ValueKind = 'text' | 'number' | 'boolean'
type ComparisonOperator = Extract<PreviewFilter, { value: unknown }>['operator']
export type AssetFilterDraft = {
  column: string
  operator: PreviewFilter['operator']
  value: string
}
type FilterOption = { value: PreviewFilter['operator']; label: string }

const comparisonOperators = [
  { value: 'eq', label: 'is' },
  { value: 'ne', label: 'is not' },
  { value: 'lt', label: 'less than' },
  { value: 'lte', label: 'at most' },
  { value: 'gt', label: 'greater than' },
  { value: 'gte', label: 'at least' },
] satisfies Array<FilterOption>
const nullOperators = [
  { value: 'is_null', label: 'is null' },
  { value: 'is_not_null', label: 'is not null' },
] satisfies Array<FilterOption>

function getIsEditableTarget(target: EventTarget | null) {
  if (!(target instanceof HTMLElement)) return false
  return (
    target.isContentEditable ||
    target instanceof HTMLInputElement ||
    target instanceof HTMLTextAreaElement ||
    target instanceof HTMLSelectElement
  )
}

function isComparisonOperator(
  operator: PreviewFilter['operator'],
): operator is ComparisonOperator {
  return comparisonOperators.some((option) => option.value === operator)
}

export function assetFilterValueKind(type: string | null): ValueKind {
  const normalized = type?.trim().toLowerCase() ?? ''
  if (/^(bool|boolean)$/.test(normalized)) return 'boolean'
  if (
    /^(tinyint|smallint|integer|int|bigint|real|float|double|decimal|numeric)(\b|\s*\()/i.test(
      normalized,
    )
  ) {
    return 'number'
  }
  return 'text'
}

export function assetFilterOperators(type: string | null): Array<FilterOption> {
  const kind = assetFilterValueKind(type)
  return [
    ...(kind === 'number'
      ? comparisonOperators
      : comparisonOperators.slice(0, 2)),
    ...nullOperators,
  ]
}

export function previewFilterLabel(filter: PreviewFilter): string {
  const label = [...comparisonOperators, ...nullOperators].find(
    (option) => option.value === filter.operator,
  )?.label
  if (!('value' in filter)) {
    return `${filter.column} ${label ?? filter.operator}`
  }
  return `${filter.column} ${label ?? filter.operator} ${String(filter.value)}`
}

function toDraft(filter: PreviewFilter): AssetFilterDraft {
  return {
    column: filter.column,
    operator: filter.operator,
    value: 'value' in filter ? String(filter.value) : '',
  }
}

export function parseAssetFilterDraft(
  draft: AssetFilterDraft,
  columns: Array<PreviewColumn>,
): PreviewFilter | null {
  const column = columns.find((item) => item.name === draft.column)
  if (!column) return null
  if (draft.operator === 'is_null' || draft.operator === 'is_not_null') {
    const parsed = previewFilterSchema.safeParse({
      column: draft.column,
      operator: draft.operator,
    })
    return parsed.success ? parsed.data : null
  }
  if (
    !isComparisonOperator(draft.operator) ||
    !assetFilterOperators(column.type).some(
      (option) => option.value === draft.operator,
    )
  ) {
    return null
  }
  const typedValue = parseFilterValue(
    draft.value,
    assetFilterValueKind(column.type),
  )
  if (!typedValue.valid) return null
  const parsed = previewFilterSchema.safeParse({
    column: draft.column,
    operator: draft.operator,
    value: typedValue.value,
  })
  return parsed.success ? parsed.data : null
}

function parseFilterValue(value: string, kind: ValueKind) {
  if (kind === 'text') return { valid: true, value }
  if (kind === 'boolean') {
    return value === 'true' || value === 'false'
      ? { valid: true, value: value === 'true' }
      : { valid: false, value: null }
  }
  const number = parseNumberValue(value)
  return number == null
    ? { valid: false, value: null }
    : { valid: true, value: number }
}

function parseNumberValue(value: string): number | null {
  const normalized = value.trim()
  if (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(normalized))
    return null
  const number = Number(normalized)
  return Number.isFinite(number) &&
    (!Number.isInteger(number) || Number.isSafeInteger(number))
    ? number
    : null
}

function defaultDraft(column: PreviewColumn): AssetFilterDraft {
  return {
    column: column.name,
    operator: 'eq',
    value: assetFilterValueKind(column.type) === 'boolean' ? 'true' : '',
  }
}

export function DataTableCommandFilterMenu({
  columns,
  filters,
  pending,
  errorMessage,
  onApply,
  onValidationError,
  onOpenChange,
}: {
  columns: AssetPreview['columns']
  filters: Array<PreviewFilter>
  pending: boolean
  errorMessage: string | null
  onApply: (filters: Array<PreviewFilter>) => Promise<boolean>
  onValidationError: () => void
  onOpenChange: (open: boolean) => void
}) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState<{
    columnId: string | null
    search: string
  }>({
    columnId: null,
    search: '',
  })
  const [drafts, setDrafts] = React.useState(filters.map(toDraft))
  const triggerRef = React.useRef<HTMLButtonElement>(null)
  const inputRef = React.useRef<HTMLInputElement>(null)
  const filterCount = filters.length
  const disabled = pending || !columns.length || filterCount >= 20

  React.useEffect(() => setDrafts(filters.map(toDraft)), [filters])

  React.useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (disabled || getIsEditableTarget(event.target)) return
      if (
        event.key.toLowerCase() !== 'f' ||
        !(event.ctrlKey || event.metaKey) ||
        !event.shiftKey
      ) {
        return
      }
      event.preventDefault()
      setOpen(!open)
      onOpenChange(!open)
      if (open) setQuery({ columnId: null, search: '' })
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [disabled, open, onOpenChange])

  const changeOpen = (next: boolean) => {
    setOpen(next)
    onOpenChange(next)
    if (!next) setQuery({ columnId: null, search: '' })
  }

  async function apply(next: Array<PreviewFilter>) {
    const succeeded = await onApply(next)
    setDrafts((succeeded ? next : filters).map(toDraft))
    return succeeded
  }

  async function addFilter(filter: PreviewFilter) {
    if (await onApply([...filters, filter])) changeOpen(false)
  }

  function updateDraft(index: number, update: Partial<AssetFilterDraft>) {
    setDrafts((current) =>
      current.map((draft, item) =>
        item === index ? { ...draft, ...update } : draft,
      ),
    )
  }

  function selectColumn(column: PreviewColumn) {
    setQuery({ columnId: column.name, search: '' })
    window.requestAnimationFrame(() => inputRef.current?.focus())
  }

  async function addSearchedValue(column: PreviewColumn, value: string) {
    const draft = { ...defaultDraft(column), value }
    const filter = parseAssetFilterDraft(draft, columns)
    if (!filter) {
      onValidationError()
      return
    }
    await addFilter(filter)
  }

  const selectedColumn = columns.find(
    (column) => column.name === query.columnId,
  )

  return (
    <div className="flex min-w-0 flex-wrap items-center gap-2">
      {filters.map((filter, index) => {
        const draft = drafts[index] ?? toDraft(filter)
        const column = columns.find((item) => item.name === draft.column)
        if (!column) return null
        const kind = assetFilterValueKind(column.type)
        const isNullOperator =
          draft.operator === 'is_null' || draft.operator === 'is_not_null'
        const options = assetFilterOperators(column.type)
        return (
          <div
            key={`${index}:${filter.column}:${filter.operator}`}
            role="group"
            aria-label={`Filter ${index + 1}: ${filter.column}`}
            className="flex min-w-0 flex-wrap items-center gap-1 rounded-lg border border-line bg-card p-1"
          >
            <FieldSelector
              column={draft.column}
              columns={columns}
              disabled={pending}
              onSelect={(selected) =>
                updateDraft(index, defaultDraft(selected))
              }
            />
            <Select
              aria-label={`Operator for ${draft.column}`}
              disabled={pending}
              className="h-7 w-auto min-w-[5.5rem] px-2 text-xs"
              value={draft.operator}
              onValueChange={(operator) =>
                updateDraft(index, {
                  operator,
                  value:
                    operator === 'is_null' || operator === 'is_not_null'
                      ? ''
                      : kind === 'boolean'
                        ? 'true'
                        : '',
                })
              }
              options={options}
            />
            {!isNullOperator && kind === 'boolean' ? (
              <Select
                aria-label={`Value for ${draft.column}`}
                disabled={pending}
                className="h-7 w-auto min-w-[4.5rem] px-2 text-xs"
                value={draft.value === 'false' ? 'false' : 'true'}
                onValueChange={(value) => updateDraft(index, { value })}
                options={[
                  { value: 'true', label: 'true' },
                  { value: 'false', label: 'false' },
                ]}
              />
            ) : !isNullOperator ? (
              <Input
                aria-label={`Value for ${draft.column}`}
                disabled={pending}
                type="text"
                inputMode={kind === 'number' ? 'decimal' : undefined}
                value={draft.value}
                onChange={(event) =>
                  updateDraft(index, { value: event.target.value })
                }
                className="h-7 w-24 px-2 text-xs"
              />
            ) : null}
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              disabled={pending}
              aria-label={`Apply filter on ${draft.column}`}
              onClick={() => {
                const parsed = parseAssetFilterDraft(draft, columns)
                if (!parsed) {
                  setDrafts(filters.map(toDraft))
                  onValidationError()
                  return
                }
                void apply(
                  filters.map((item, itemIndex) =>
                    itemIndex === index ? parsed : item,
                  ),
                )
              }}
            >
              <CheckIcon aria-hidden className="size-3.5" />
            </Button>
            <Button
              variant="ghost"
              size="icon"
              className="size-7"
              disabled={pending}
              aria-label={`Remove filter ${index + 1} on ${filter.column}`}
              onClick={() =>
                void apply(
                  filters.filter((_, itemIndex) => itemIndex !== index),
                ).then((succeeded) => succeeded && triggerRef.current?.focus())
              }
            >
              <XIcon aria-hidden className="size-3.5" />
            </Button>
          </div>
        )
      })}
      {filterCount > 0 ? (
        <Button
          variant="outline"
          size="icon"
          className="size-8"
          disabled={pending}
          aria-label="Reset all filters"
          onClick={() => void apply([])}
        >
          <XIcon aria-hidden className="size-4" />
        </Button>
      ) : null}
      <Popover open={open} onOpenChange={changeOpen}>
        <PopoverTrigger
          render={
            <Button
              ref={triggerRef}
              variant="outline"
              className="h-8 border-dashed"
              disabled={disabled}
              aria-label="Open filter command menu"
              onKeyDown={(event) => {
                if (
                  filters.length &&
                  (event.key === 'Backspace' || event.key === 'Delete')
                ) {
                  event.preventDefault()
                  void apply(filters.slice(0, -1))
                }
              }}
            />
          }
        >
          <ListFilterIcon aria-hidden className="size-4" />
          Filter
          {filters.length ? (
            <span className="rounded bg-soft px-1.5 font-mono text-[11px]">
              {filters.length}
            </span>
          ) : null}
          <PlusIcon aria-hidden className="size-3" />
        </PopoverTrigger>
        <PopoverContent className="w-[min(320px,calc(100vw-2rem))] p-0">
          <Command label="Filter asset data" loop>
            <CommandInput
              ref={inputRef}
              disabled={pending}
              placeholder={selectedColumn?.name ?? 'Search fields...'}
              value={query.search}
              onValueChange={(search) =>
                setQuery((current) => ({ ...current, search }))
              }
              onKeyDown={(event) => {
                if (
                  selectedColumn &&
                  !query.search &&
                  (event.key === 'Backspace' || event.key === 'Delete')
                ) {
                  event.preventDefault()
                  setQuery({ columnId: null, search: '' })
                }
              }}
            />
            <CommandList inert={pending}>
              {selectedColumn ? (
                <FilterValues
                  column={selectedColumn}
                  search={query.search}
                  onSelect={(value) =>
                    void addSearchedValue(selectedColumn, value)
                  }
                  onNull={(operator) =>
                    void addFilter({ column: selectedColumn.name, operator })
                  }
                />
              ) : (
                <>
                  <CommandEmpty>No fields found.</CommandEmpty>
                  <CommandGroup>
                    {columns.map((column) => (
                      <CommandItem
                        key={column.name}
                        value={`${column.name} ${column.type ?? ''}`}
                        onSelect={() => selectColumn(column)}
                      >
                        <span
                          className="min-w-0 flex-1 truncate"
                          title={column.name}
                        >
                          {column.name}
                        </span>
                        <span
                          className="max-w-[55%] shrink-0 truncate text-xs text-muted-foreground"
                          title={column.type ?? 'unknown'}
                        >
                          {column.type ?? 'unknown'}
                        </span>
                      </CommandItem>
                    ))}
                  </CommandGroup>
                </>
              )}
            </CommandList>
            {errorMessage ? (
              <p
                role="alert"
                className="m-0 border-t border-line px-3 py-2 text-xs text-bad-text"
              >
                {errorMessage} The previous preview is unchanged.
              </p>
            ) : null}
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}

function FieldSelector({
  column,
  columns,
  disabled,
  onSelect,
}: {
  column: string
  columns: Array<PreviewColumn>
  disabled: boolean
  onSelect: (column: PreviewColumn) => void
}) {
  const [open, setOpen] = React.useState(false)
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            disabled={disabled}
            className="h-7 max-w-[150px] min-w-0 justify-start rounded-md px-2 text-xs"
            aria-label={`Change field ${column}`}
          />
        }
      >
        <span className="truncate">{column}</span>
      </PopoverTrigger>
      <PopoverContent className="w-[min(280px,calc(100vw-2rem))] p-0">
        <Command label="Change filter field">
          <CommandInput placeholder="Search fields..." />
          <CommandList>
            <CommandEmpty>No fields found.</CommandEmpty>
            <CommandGroup>
              {columns.map((option) => (
                <CommandItem
                  key={option.name}
                  value={`${option.name} ${option.type ?? ''}`}
                  onSelect={() => {
                    onSelect(option)
                    setOpen(false)
                  }}
                >
                  <span className="min-w-0 flex-1 truncate" title={option.name}>
                    {option.name}
                  </span>
                  <span
                    className="max-w-[55%] shrink-0 truncate text-xs text-muted-foreground"
                    title={option.type ?? 'unknown'}
                  >
                    {option.type ?? 'unknown'}
                  </span>
                </CommandItem>
              ))}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function FilterValues({
  column,
  search,
  onSelect,
  onNull,
}: {
  column: PreviewColumn
  search: string
  onSelect: (value: string) => void
  onNull: (operator: 'is_null' | 'is_not_null') => void
}) {
  const kind = assetFilterValueKind(column.type)
  const trimmed = search.trim()
  const parsedNumber =
    kind === 'number' && trimmed ? parseNumberValue(trimmed) : null
  const numberIsValid = parsedNumber != null

  return (
    <>
      {kind === 'boolean' ? (
        <CommandGroup>
          {['true', 'false'].map((value) => (
            <CommandItem
              key={value}
              value={value}
              onSelect={() => onSelect(value)}
            >
              {value === 'true' ? 'True' : 'False'}
            </CommandItem>
          ))}
        </CommandGroup>
      ) : (
        <>
          <CommandEmpty>
            No matching values. Type a value to filter.
          </CommandEmpty>
          <CommandGroup>
            <CommandItem
              value={search}
              disabled={!trimmed || (kind === 'number' && !numberIsValid)}
              onSelect={() => onSelect(search)}
            >
              {trimmed ? `Filter by “${search}”` : 'Type to add a filter…'}
            </CommandItem>
          </CommandGroup>
        </>
      )}
      <CommandGroup>
        <CommandItem value="is null" onSelect={() => onNull('is_null')}>
          {column.name} is null
        </CommandItem>
        <CommandItem value="is not null" onSelect={() => onNull('is_not_null')}>
          {column.name} is not null
        </CommandItem>
      </CommandGroup>
      {kind === 'number' && trimmed && !numberIsValid ? (
        <p
          role="alert"
          className="m-0 border-t border-line px-3 py-2 text-xs text-bad-text"
        >
          Enter a finite, safely representable number.
        </p>
      ) : null}
    </>
  )
}
