/**
 * Page Header component.
 */
import type { ReadEvidence } from "@/api/types";
import { cn } from "@/lib/utils";

/**
 * Page masthead: 25px display title, optional status/example chips, muted
 * subtitle and a right-aligned action slot. Matches Paper's `Dense heading`.
 */
export function PageHeader({
  title,
  titleAccessory,
  description,
  actions,
  className,
  children,
}: {
  title: string;
  titleAccessory?: React.ReactNode;
  description?: React.ReactNode;
  actions?: React.ReactNode;
  className?: string;
  children?: React.ReactNode;
}) {
  return (
    <header className={cn("flex items-start justify-between gap-4 px-6 pt-4.75 pb-4.25", className)}>
      <div className="flex min-w-0 flex-col gap-1.5">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="font-display text-[25px] leading-7.5 font-semibold tracking-[-0.03em] text-foreground">
            {title}
          </h1>
          {titleAccessory}
        </div>
        {description ? (
          <div className="text-xs leading-4 text-muted-foreground">{description}</div>
        ) : null}
        {children}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </header>
  );
}

/** Evidence-driven read-state marker rendered beside page titles.
 *
 * Only states a reader must not miss are labelled: demo fixtures, stale
 * last-confirmed answers, unsupported/unavailable surfaces, partial data and
 * a recorded-nothing collection. Healthy live data renders no chip at all.
 */
export function ReadStateChip({
  evidence,
  className,
}: {
  evidence?: ReadEvidence;
  className?: string;
}) {
  let label: string | null = null;
  if (!evidence) {
    return null;
  }
  if (evidence.status === "demo") {
    label = "Demo data";
  } else if (evidence.status === "stale") {
    const confirmed = evidence.last_confirmed_at
      ? new Date(evidence.last_confirmed_at)
      : null;
    label =
      confirmed && !Number.isNaN(confirmed.getTime())
        ? `Stale · last confirmed ${confirmed.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}`
        : "Stale data";
  } else if (evidence.status === "unsupported") {
    label = "Not supported";
  } else if (evidence.status === "unavailable") {
    label = "Unavailable";
  } else if (evidence.reason_code === "partial" || evidence.dropped_records > 0) {
    label = "Partial data";
  } else if (evidence.reason_code === "absent") {
    label = "No records yet";
  }
  if (!label) {
    return null;
  }
  return (
    <span
      className={cn(
        "rounded-[4px] border border-border px-1.5 py-[3px] text-[10px] leading-3 text-muted-foreground",
        className,
      )}
    >
      {label}
    </span>
  );
}
