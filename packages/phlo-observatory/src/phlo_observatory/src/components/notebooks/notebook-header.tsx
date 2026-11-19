/** The notebook's header: name, kernel, save and run, and the Share menu; and the path line below it. */
import * as React from 'react'
import {
  CalendarClockIcon,
  ChevronDownIcon,
  CopyIcon,
  EyeIcon,
  FilesIcon,
  GitBranchIcon,
  GitCommitHorizontalIcon,
  LinkIcon,
  LockIcon,
  PackageIcon,
  PencilIcon,
  PlayIcon,
  PlusIcon,
  SquareIcon,
  Trash2Icon,
} from 'lucide-react'
import type { NotebookAccess } from '@/lib/data/api/notebooks'
import type { KernelPhase } from '@/components/notebooks/use-kernel'
import type { FileAction } from '@/components/notebooks/file-dialogs'
import { KernelMenu } from '@/components/notebooks/kernel-menu'
import { SaveStatus } from '@/components/notebooks/notebook-panels'
import { Mono } from '@/components/phlo/status'
import { Badge } from '@/components/ui/badge'
import { Button, buttonVariants } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/menu'
import { cn } from '@/lib/utils'

function fileName(path: string) {
  return path.split('/').at(-1) ?? path
}

function ShareMenu({
  path,
  access,
  hasSchedule,
  packageCount,
  onDialog,
  onFile,
}: {
  path: string
  access: NotebookAccess
  hasSchedule: boolean
  packageCount: number
  onDialog: (dialog: 'packages' | 'schedule' | 'git') => void
  onFile: (action: FileAction | { kind: 'link'; path: string }) => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
      >
        Share <ChevronDownIcon className="text-faint" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-60">
        <DropdownMenuItem onClick={() => onFile({ kind: 'link', path })}>
          <LinkIcon /> Copy link
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!access.publish}
          onClick={() => onDialog('git')}
        >
          <GitCommitHorizontalIcon /> Commit to git…
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!access.schedule}
          onClick={() => onDialog('schedule')}
        >
          <CalendarClockIcon /> {hasSchedule ? 'Edit schedule…' : 'Schedule…'}
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => onDialog('packages')}>
          <PackageIcon /> Packages…
          {packageCount ? (
            <span className="ml-auto text-xs text-muted-foreground">
              {packageCount}
            </span>
          ) : null}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuLabel>File</DropdownMenuLabel>
        <DropdownMenuItem
          disabled={!access.edit}
          onClick={() => onFile({ kind: 'rename', path })}
        >
          <PencilIcon /> Rename or move…
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!access.edit}
          onClick={() => onFile({ kind: 'duplicate', path })}
        >
          <CopyIcon /> Duplicate…
        </DropdownMenuItem>
        <DropdownMenuItem
          disabled={!access.edit}
          onClick={() => onFile({ kind: 'delete', path })}
        >
          <Trash2Icon /> Delete…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function NotebookHeader({
  path,
  nessieRef,
  dirty,
  access,
  sidebar,
  phase,
  kernelId,
  env,
  saving,
  canRunAll,
  hasSchedule,
  packageCount,
  onNew,
  onKernel,
  onStart,
  onSave,
  onRunAll,
  onDialog,
  onFile,
}: {
  path: string
  nessieRef: string
  dirty: boolean
  access: NotebookAccess
  /** Files and catalog, shown in a popover below lg */
  sidebar: React.ReactNode
  phase: KernelPhase
  kernelId: string | null
  env: string
  saving: boolean
  canRunAll: boolean
  hasSchedule: boolean
  packageCount: number
  onNew: () => void
  onKernel: (action: 'interrupt' | 'restart' | 'stop') => void
  onStart: () => void
  onSave: () => void
  onRunAll: () => void
  onDialog: (dialog: 'packages' | 'schedule' | 'git') => void
  onFile: (action: FileAction | { kind: 'link'; path: string }) => void
}) {
  return (
    <header className="flex min-h-[52px] shrink-0 flex-wrap items-center gap-x-2.5 gap-y-2 border-b border-line px-4 py-2.5 lg:pl-5">
      <h1 className="mr-2 text-sm font-medium">Notebooks</h1>
      <span className="inline-flex h-7 min-w-0 items-center gap-2 rounded-md bg-soft px-2.5 text-[13px] text-foreground">
        <span className="truncate">{fileName(path)}</span>
        {dirty ? (
          <span
            aria-label="Unsaved changes"
            className="size-1.5 rounded-full bg-text-3"
          />
        ) : null}
      </span>
      <Button
        variant="ghost"
        size="sm"
        aria-label="New notebook"
        className="ml-auto lg:ml-0"
        disabled={!access.edit}
        onClick={onNew}
      >
        <PlusIcon />
        <span className="max-sm:hidden">Notebook</span>
      </Button>
      <div className="flex w-full flex-wrap items-center gap-2 lg:ml-auto lg:w-auto lg:justify-end">
        <Popover>
          <PopoverTrigger
            className={cn(
              buttonVariants({ variant: 'outline', size: 'sm' }),
              'lg:hidden',
            )}
          >
            <FilesIcon /> Browse
          </PopoverTrigger>
          <PopoverContent className="max-h-[70dvh] w-[300px] overflow-y-auto p-2.5">
            {sidebar}
          </PopoverContent>
        </Popover>
        <span
          title="Lakehouse SQL cells read this environment's configured Nessie ref"
          className={cn(
            buttonVariants({ variant: 'outline', size: 'sm' }),
            'hidden md:inline-flex',
          )}
        >
          <GitBranchIcon className="text-branch" />
          <Mono>{nessieRef}</Mono>
        </span>
        <KernelMenu
          env={env}
          phase={phase}
          kernelId={kernelId}
          onStart={onStart}
          onRestart={() => onKernel('restart')}
          onInterrupt={() => onKernel('interrupt')}
          onStop={() => onKernel('stop')}
        />
        <ShareMenu
          path={path}
          access={access}
          hasSchedule={hasSchedule}
          packageCount={packageCount}
          onDialog={onDialog}
          onFile={onFile}
        />
        {phase === 'busy' ? (
          <Button
            variant="outline"
            size="sm"
            onClick={() => onKernel('interrupt')}
          >
            <SquareIcon className="fill-current" /> Interrupt
          </Button>
        ) : null}
        {access.edit ? (
          <Button
            variant="outline"
            size="sm"
            disabled={!dirty || saving}
            onClick={onSave}
          >
            {saving ? 'Saving…' : 'Save'}
          </Button>
        ) : null}
        <Button size="sm" disabled={!canRunAll} onClick={onRunAll}>
          <PlayIcon className="fill-current" /> Run all
        </Button>
      </div>
    </header>
  )
}

export function NotebookSubheader({
  path,
  access,
  saveState,
  saveError,
  dirty,
  onReload,
}: {
  path: string
  access: NotebookAccess
  saveState: 'idle' | 'saving' | 'conflict' | 'error'
  saveError: string | null
  dirty: boolean
  onReload: () => void
}) {
  return (
    <div className="flex min-h-[36px] shrink-0 flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-4 py-1.5 text-xs text-muted-foreground lg:pl-5">
      <Mono className="text-text-2">notebooks/{path}</Mono>
      {access.edit ? (
        <SaveStatus
          state={saveState}
          dirty={dirty}
          error={saveError}
          onReload={onReload}
        />
      ) : (
        <Badge variant="outline" size="sm">
          <EyeIcon className="size-3" /> Read only
        </Badge>
      )}
      <span className="hidden items-center gap-1.5 md:inline-flex">
        <LockIcon className="size-3" /> Reads the lakehouse only. Nothing here
        writes to it.
      </span>
      <span className="ml-auto hidden items-center gap-1.5 lg:inline-flex">
        Run cell
        <kbd className="rounded border border-border bg-raised px-1 font-mono text-[11px]">
          ⇧↵
        </kbd>
        Complete
        <kbd className="rounded border border-border bg-raised px-1 font-mono text-[11px]">
          ⌃Space
        </kbd>
      </span>
    </div>
  )
}
