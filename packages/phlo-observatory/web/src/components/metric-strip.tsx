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
 * Paper's summary band. Each cell paints a subtle surface with top and bottom
 * hairlines; `dividers` adds the vertical rule used on dense registry pages.
 * The left edge is intentionally open, so the band reads as a strip.
 */
export function MetricStrip({
  metrics,
  className,
  dividers = true,
}: {
  metrics: Array<Metric>;
  className?: string;
  dividers?: boolean;
}) {
  return (
    <div className={cn("flex", className)}>
      {metrics.map((metric) => (
        <div
          key={metric.label}
          className={cn(
            "flex min-w-0 flex-1 flex-col gap-1.5 border-y border-border bg-subtle p-3",
            dividers && "border-r",
          )}
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
