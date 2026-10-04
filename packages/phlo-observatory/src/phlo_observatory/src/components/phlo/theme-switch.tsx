/** Provides controls for selecting and toggling the active colour theme. */
import { MoonIcon, SunIcon } from 'lucide-react'
import { useTheme } from '@/lib/theme'
import { cn } from '@/lib/utils'

/** Light / Dark segmented switch (sidebar) — shares one setting with every screen and tab. */
export function ThemeSwitch({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme()
  const btn = (t: 'light' | 'dark', label: string, Icon: typeof SunIcon) => (
    <button
      type="button"
      aria-pressed={theme === t}
      onClick={() => setTheme(t)}
      className={cn(
        'flex h-[26px] flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-md text-[12.5px] text-text-3',
        theme === t &&
          'bg-card text-foreground shadow-[0_0_0_1px_var(--border)]',
      )}
    >
      <Icon className="size-3.5" />
      {label}
    </button>
  )
  return (
    <div
      role="group"
      aria-label="Theme"
      className={cn(
        'flex rounded-lg border border-border bg-raised p-0.5',
        className,
      )}
    >
      {btn('light', 'Light', SunIcon)}
      {btn('dark', 'Dark', MoonIcon)}
    </div>
  )
}

/** Icon-only toggle for the phone header. */
export function ThemeToggleButton({ className }: { className?: string }) {
  const { theme, setTheme } = useTheme()
  const next = theme === 'dark' ? 'light' : 'dark'
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      aria-label={`Switch to ${next} mode`}
      className={cn(
        'inline-flex size-11 cursor-pointer items-center justify-center rounded-lg text-text-3 hover:bg-soft',
        className,
      )}
    >
      {theme === 'dark' ? (
        <SunIcon className="size-[18px]" />
      ) : (
        <MoonIcon className="size-[18px]" />
      )}
    </button>
  )
}
