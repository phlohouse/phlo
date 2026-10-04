/** Wraps Recharts with theme-aware configuration, tooltips, and legends. */
import * as React from 'react'
import * as RechartsPrimitive from 'recharts'
import { cn } from '@/lib/utils'

/**
 * shadcn-style chart wrapper on Recharts.
 *
 * - `config` names each series and gives it a colour token (`var(--color-bad)` etc).
 *   Each entry is exposed as `--color-<key>` on the container, so series use
 *   `fill="var(--color-failed)"` / `stroke="var(--color-do)"` and both themes just work.
 * - Axes, grid and tooltip pick up phlo tokens from the base classes below.
 */
export type ChartConfig = Record<
  string,
  {
    label?: React.ReactNode
    /** a CSS colour, normally a token: 'var(--color-ok)' */
    color?: string
    icon?: React.ComponentType
  }
>

type ChartContextProps = { config: ChartConfig }
const ChartContext = React.createContext<ChartContextProps | null>(null)

export function useChart() {
  const ctx = React.useContext(ChartContext)
  if (!ctx) throw new Error('useChart must be used inside <ChartContainer />')
  return ctx
}

export function ChartContainer({
  id,
  className,
  children,
  config,
  label,
  ...props
}: React.ComponentProps<'div'> & {
  config: ChartConfig
  /** Screen-reader summary of what the chart shows. */
  label: string
  children: React.ComponentProps<
    typeof RechartsPrimitive.ResponsiveContainer
  >['children']
}) {
  const uid = React.useId()
  const chartId = `chart-${id ?? uid.replace(/:/g, '')}`
  const style = Object.fromEntries(
    Object.entries(config)
      .filter(([, v]) => v.color)
      .map(([k, v]) => [`--color-${k}`, v.color]),
  ) as React.CSSProperties

  return (
    <ChartContext.Provider value={{ config }}>
      <div
        data-slot="chart"
        data-chart={chartId}
        role="img"
        aria-label={label}
        style={style}
        className={cn(
          'flex aspect-video justify-center text-xs',
          '[&_.recharts-cartesian-axis-tick_text]:fill-muted-foreground [&_.recharts-cartesian-axis-tick_text]:font-mono',
          // recharts 3.x renders tick text as .recharts-cartesian-axis-tick-value
          '[&_.recharts-cartesian-axis-tick-value]:fill-muted-foreground [&_.recharts-cartesian-axis-tick-value]:font-mono',
          '[&_.recharts-cartesian-grid_line]:stroke-grid [&_.recharts-cartesian-axis-line]:stroke-grid',
          '[&_.recharts-curve.recharts-tooltip-cursor]:stroke-border-strong [&_.recharts-rectangle.recharts-tooltip-cursor]:fill-soft',
          "[&_.recharts-reference-line_line]:stroke-border-strong [&_.recharts-dot[stroke='#fff']]:stroke-transparent",
          '[&_.recharts-layer]:outline-none [&_.recharts-sector]:outline-none [&_.recharts-surface]:outline-none',
          className,
        )}
        {...props}
      >
        <RechartsPrimitive.ResponsiveContainer
          initialDimension={{ width: 320, height: 180 }}
        >
          {children}
        </RechartsPrimitive.ResponsiveContainer>
      </div>
    </ChartContext.Provider>
  )
}

export const ChartTooltip = RechartsPrimitive.Tooltip
export const ChartLegend = RechartsPrimitive.Legend

type Payload = {
  dataKey?: string | number | ((o: unknown) => unknown)
  name?: string | number
  value?: unknown
  color?: string
  fill?: string
  payload?: Record<string, unknown>
}

/** Tooltip body. Pass as `content={<ChartTooltipContent />}`. */
export function ChartTooltipContent({
  active,
  payload,
  label,
  labelFormatter,
  valueFormatter,
  hideLabel,
  indicator = 'dot',
  className,
}: {
  active?: boolean
  payload?: ReadonlyArray<Payload>
  label?: React.ReactNode
  labelFormatter?: (
    label: React.ReactNode,
    payload: ReadonlyArray<Payload>,
  ) => React.ReactNode
  valueFormatter?: (value: unknown, key: string) => React.ReactNode
  hideLabel?: boolean
  indicator?: 'dot' | 'line'
  className?: string
}) {
  const { config } = useChart()
  if (!active || !payload?.length) return null
  const shown = payload.filter((p) => p.value !== undefined && p.value !== null)
  return (
    <div
      className={cn(
        'grid min-w-32 gap-1.5 rounded-lg border border-border bg-card px-2.5 py-1.5 text-xs text-foreground shadow-lg',
        className,
      )}
    >
      {!hideLabel ? (
        <div className="font-medium">
          {labelFormatter ? labelFormatter(label, payload) : label}
        </div>
      ) : null}
      {shown.map((p, i) => {
        const key = String(p.dataKey ?? p.name ?? i)
        const cfg = config[key]
        const colour = cfg?.color ?? p.color ?? p.fill
        return (
          <div key={key} className="flex items-center gap-2">
            <span
              aria-hidden
              className={cn(
                'shrink-0',
                indicator === 'dot' ? 'size-2 rounded-[2px]' : 'h-0.5 w-3',
              )}
              style={{ background: colour }}
            />
            <span className="text-muted-foreground">
              {cfg?.label ?? p.name}
            </span>
            <span className="ml-auto pl-3 font-mono tabular-nums">
              {valueFormatter ? valueFormatter(p.value, key) : String(p.value)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

/** Legend body. Pass as `content={<ChartLegendContent />}`. */
export function ChartLegendContent({
  payload,
  className,
}: {
  payload?: ReadonlyArray<{
    dataKey?: unknown
    value?: unknown
    color?: string
  }>
  className?: string
}) {
  const { config } = useChart()
  if (!payload?.length) return null
  return (
    <div
      className={cn(
        'flex flex-wrap items-center justify-end gap-3 pb-2 text-xs text-muted-foreground',
        className,
      )}
    >
      {payload.map((p, i) => {
        const key = String(p.dataKey ?? p.value ?? i)
        const cfg = config[key]
        return (
          <span key={key} className="inline-flex items-center gap-1.5">
            <span
              aria-hidden
              className="size-2 rounded-[2px]"
              style={{ background: cfg?.color ?? p.color }}
            />
            {cfg?.label ?? String(p.value)}
          </span>
        )
      })}
    </div>
  )
}
