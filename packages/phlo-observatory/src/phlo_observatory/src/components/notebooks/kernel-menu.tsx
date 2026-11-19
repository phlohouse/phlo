/** Kernel status pill that opens kernel controls and lists the caller's running kernels. */
import * as React from 'react'
import {
  ChevronDownIcon,
  PlayIcon,
  PowerIcon,
  RotateCcwIcon,
  SquareIcon,
} from 'lucide-react'
import type { KernelView } from '@/lib/data/api/notebooks'
import type { KernelPhase } from '@/components/notebooks/use-kernel'
import { controlKernel, listKernels } from '@/lib/data/api/notebooks'
import { Dot } from '@/components/phlo/status'
import { buttonVariants } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/menu'
import { cn } from '@/lib/utils'

const labels: Record<KernelPhase, string> = {
  disabled: 'Kernels off',
  off: 'Python 3 · stopped',
  installing: 'Installing packages',
  starting: 'Python 3 · starting',
  failed: 'Kernel failed',
  idle: 'Python 3 · idle',
  busy: 'Python 3 · busy',
}

function tone(phase: KernelPhase) {
  if (phase === 'idle') return 'ok' as const
  if (phase === 'failed') return 'bad' as const
  if (phase === 'busy' || phase === 'starting' || phase === 'installing')
    return 'warn' as const
  return 'neutral' as const
}

export function KernelMenu({
  env,
  phase,
  kernelId,
  onStart,
  onRestart,
  onInterrupt,
  onStop,
}: {
  env: string
  phase: KernelPhase
  /** This notebook's kernel, if one is running */
  kernelId: string | null
  onStart: () => void
  onRestart: () => void
  onInterrupt: () => void
  onStop: () => void
}) {
  const [others, setOthers] = React.useState<Array<KernelView> | null>(null)
  const refresh = React.useCallback(() => {
    setOthers(null)
    listKernels({ data: { env } }).then(
      (result) => setOthers(result.state === 'ok' ? result.data : []),
      () => setOthers([]),
    )
  }, [env])
  const running = phase === 'idle' || phase === 'busy'
  const elsewhere = (others ?? []).filter((kernel) => kernel.id !== kernelId)

  return (
    <DropdownMenu
      onOpenChange={(open) => {
        if (open && phase !== 'disabled') refresh()
      }}
    >
      <DropdownMenuTrigger
        aria-label={`Kernel: ${labels[phase]}`}
        className={cn(buttonVariants({ variant: 'outline', size: 'sm' }))}
      >
        <Dot tone={tone(phase)} />
        {labels[phase]}
        <ChevronDownIcon className="text-faint" />
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-64">
        {phase === 'disabled' ? (
          <DropdownMenuLabel className="text-xs font-normal whitespace-normal text-muted-foreground">
            An operator turns kernels on with PHLO_NOTEBOOK_KERNELS=1.
          </DropdownMenuLabel>
        ) : (
          <>
            <DropdownMenuLabel>This notebook</DropdownMenuLabel>
            {running ? null : (
              <DropdownMenuItem
                disabled={phase === 'starting' || phase === 'installing'}
                onClick={onStart}
              >
                <PlayIcon /> {phase === 'failed' ? 'Try again' : 'Start kernel'}
              </DropdownMenuItem>
            )}
            {phase === 'busy' ? (
              <DropdownMenuItem onClick={onInterrupt}>
                <SquareIcon /> Interrupt
              </DropdownMenuItem>
            ) : null}
            <DropdownMenuItem disabled={!running} onClick={onRestart}>
              <RotateCcwIcon /> Restart kernel
            </DropdownMenuItem>
            <DropdownMenuItem disabled={!running} onClick={onStop}>
              <PowerIcon /> Stop kernel
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuLabel>Your other kernels</DropdownMenuLabel>
            {others === null ? (
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                Loading…
              </DropdownMenuLabel>
            ) : elsewhere.length === 0 ? (
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                None running
              </DropdownMenuLabel>
            ) : (
              elsewhere.map((kernel) => (
                <DropdownMenuItem
                  key={kernel.id}
                  aria-label={`Stop the kernel for ${kernel.path}`}
                  onClick={() => {
                    void controlKernel({
                      data: { env, kernel: kernel.id, action: 'stop' },
                    }).finally(refresh)
                  }}
                >
                  <PowerIcon />
                  <span className="min-w-0 flex-1 truncate font-mono text-[12px]">
                    {kernel.path}
                  </span>
                  <span className="text-[11px] text-muted-foreground">
                    Stop
                  </span>
                </DropdownMenuItem>
              ))
            )}
          </>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
