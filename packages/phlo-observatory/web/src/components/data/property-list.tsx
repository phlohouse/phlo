/**
 * Property List component.
 */
import { cn } from "@/lib/utils";

export interface PropertyRow {
  label: React.ReactNode;
  value: React.ReactNode;
}

/**
 * Label/value rows used by every inspector rail. 12px rows, muted label,
 * right-aligned value, hairline separators.
 */
export function PropertyList({
  rows,
  className,
  divided = true,
}: {
  rows: Array<PropertyRow>;
  className?: string;
  divided?: boolean;
}) {
  return (
    <dl className={cn("flex flex-col", className)}>
      {rows.map((row, i) => (
        <div
          key={i}
          className={cn(
            "flex min-h-7 items-center justify-between gap-2.5 text-xs",
            divided && "border-b border-border/60 last:border-b-0",
          )}
        >
          <dt className="text-muted-foreground">{row.label}</dt>
          <dd className="font-medium text-foreground">{row.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Compact inline key/value pair without separators. */
export function DetailRow({
  label,
  value,
  className,
}: {
  label: React.ReactNode;
  value: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between gap-2.5 text-[11px] leading-3.5", className)}>
      <span className="text-muted-foreground">{label}</span>
      <span className="text-foreground">{value}</span>
    </div>
  );
}
