/**
 * Action buttons and confirmation for Observatory actions. High/critical
 * risk actions confirm through an AlertDialog; everything else runs on
 * click. Results surface through the toast hook.
 */
import { AlertTriangle, Loader2, Play } from 'lucide-react'
import { useState } from 'react'
import type { ReactNode } from 'react'

import type {
  ObservatoryAction,
  ObservatoryActionResult,
  ObservatoryResourceResult,
} from '@/observatory/api/types'
import { Button } from '@/components/ui/button'
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/ui/alert-dialog'
import { useToast } from '@/hooks/use-toast'

export function needsConfirmation(action: ObservatoryAction): boolean {
  return (
    action.requires_confirmation ||
    action.risk_level === 'high' ||
    action.risk_level === 'critical'
  )
}

export function ObservActionButton({
  action,
  onRun,
  size = 'xs',
}: {
  action: ObservatoryAction
  onRun: (action: ObservatoryAction) => void
  size?: 'xs' | 'sm' | 'default'
}) {
  const risky = action.risk_level === 'high' || action.risk_level === 'critical'
  const Icon = risky ? AlertTriangle : Play
  return (
    <Button
      disabled={!action.enabled}
      onClick={() => onRun(action)}
      size={size}
      title={action.reason ?? action.equivalent_cli_command ?? action.label}
      variant={risky ? 'destructive' : 'outline'}
    >
      <Icon className="size-3" />
      {action.label}
    </Button>
  )
}

export function ConfirmActionDialog({
  action,
  open,
  onCancel,
  onConfirm,
  pending,
}: {
  action: ObservatoryAction | null
  open: boolean
  onCancel: () => void
  onConfirm: () => void
  pending?: boolean
}) {
  if (!action) return null
  return (
    <AlertDialog onOpenChange={(next) => !next && onCancel()} open={open}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{action.label}</AlertDialogTitle>
          <AlertDialogDescription>
            {action.reason ??
              `Run “${action.label}” against the lakehouse runtime.`}
            {action.equivalent_cli_command && (
              <>
                {' '}
                Equivalent CLI:{' '}
                <code className="font-mono">
                  {action.equivalent_cli_command}
                </code>
              </>
            )}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel onClick={onCancel}>Cancel</AlertDialogCancel>
          <Button
            disabled={pending}
            onClick={onConfirm}
            size="sm"
            variant={
              action.risk_level === 'high' || action.risk_level === 'critical'
                ? 'destructive'
                : 'default'
            }
          >
            {pending && <Loader2 className="size-3.5 animate-spin" />}
            Run action
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/**
 * Shared runner: asks for confirmation when required, executes, and toasts
 * the outcome. Returns a trigger and the dialog element to mount.
 */
export function useActionRunner(
  run: (
    actionId: string,
  ) => Promise<ObservatoryResourceResult<ObservatoryActionResult>>,
  options?: { onSettled?: () => void },
) {
  const { toast } = useToast()
  const [pendingAction, setPendingAction] = useState<ObservatoryAction | null>(
    null,
  )
  const [running, setRunning] = useState(false)

  const request = (action: ObservatoryAction) => {
    if (needsConfirmation(action)) {
      setPendingAction(action)
    } else {
      void execute(action)
    }
  }

  const execute = async (action: ObservatoryAction) => {
    setRunning(true)
    try {
      const result = await run(action.id)
      if (result.error) {
        toast({
          description: result.error,
          title: `${action.label} failed`,
        })
      } else {
        toast({
          description: result.data?.message ?? `${action.label} finished.`,
          title: `${action.label} ${result.data?.status ?? 'completed'}`,
        })
      }
    } finally {
      setRunning(false)
      setPendingAction(null)
      options?.onSettled?.()
    }
  }

  const dialog = (
    <ConfirmActionDialog
      action={pendingAction}
      onCancel={() => setPendingAction(null)}
      onConfirm={() => pendingAction && void execute(pendingAction)}
      open={pendingAction !== null}
      pending={running}
    />
  )

  return { dialog, request, running }
}

export function ActionBar({ children }: { children: ReactNode }) {
  return <div className="flex flex-wrap items-center gap-1.5">{children}</div>
}
