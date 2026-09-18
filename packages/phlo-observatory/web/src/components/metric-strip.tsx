/**
 * Metric Strip component.
 */
import { cn } from "@/lib/utils";

export interface Metric {
  label: string;
  value: string;
  hint?: string;
  /** Tailwind text colour override for the value, e.g. `text-success`. */
  tone?: string;
}

/**
 * Paper's summary band. Each cell paints a subtle surface with top, bottom and
 * right hairlines; the left edge is intentionally open, so the band reads as a
 * strip rather than a card.
 */
export function MetricStrip({ metrics, className }: { metrics: Metric[]; className?: string }) {
  return (
    <div className={cn("flex", className)}>
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className="flex min-w-0 flex-1 flex-col gap-1.5 border-y border-r border-border bg-subtle p-3"
        >
          <div className="truncate text-[11px] leading-3.5 text-muted-foreground">
            {metric.label}
          </div>
          <div
            className={cn(
              "truncate font-display text-[17px] leading-5.5 font-semibold tracking-[-0.02em] text-foreground",
              metric.tone,
            )}
          >
            {metric.value}
          </div>
          {metric.hint ? (
            <div className="truncate text-[10px] leading-3 text-muted-foreground">{metric.hint}</div>
          ) : null}
        </div>
      ))}
    </div>
  );
}
