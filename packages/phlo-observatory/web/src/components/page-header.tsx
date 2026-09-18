/**
 * Page Header component.
 */
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
    <header className={cn("flex items-start justify-between gap-4 pt-4.75 pb-4.25", className)}>
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

/** Small bordered "Example data" marker used beside page titles. */
export function ExampleDataChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "rounded-[4px] border border-border px-1.5 py-[3px] text-[10px] leading-3 text-muted-foreground",
        className,
      )}
    >
      Example data
    </span>
  );
}
