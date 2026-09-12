/**
 * Toaster Component
 *
 * Renders toast notifications. Place in root layout.
 */

import { useToast } from '@/hooks/use-toast'
import { cn } from '@/lib/utils'

export function Toaster() {
  const { toasts } = useToast()

  return (
    <div className="fixed bottom-4 right-4 z-50 flex flex-col gap-2">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={cn(
            'pointer-events-auto rounded-none border border-rule bg-sheet p-4 transition-[opacity,transform] duration-200 ease-out',
            toast.open
              ? 'animate-in slide-in-from-bottom-5'
              : 'animate-out fade-out-80 slide-out-to-right-full',
          )}
        >
          {toast.title && (
            <div className="text-sm font-semibold">{toast.title}</div>
          )}
          {toast.description && (
            <div className="text-sm text-muted-foreground">
              {toast.description}
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
