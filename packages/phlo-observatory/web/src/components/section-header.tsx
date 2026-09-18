/**
 * Section Header component.
 */
import { cn } from "@/lib/utils";

/**
 * Section heading: 15px display title with an optional trailing meta slot or
 * accent link. Reproduced from Paper's repeated `flex justify-between pb-2.5`.
 */
export function SectionHeader({
  title,
  meta,
  action,
  className,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex items-center justify-between pb-2.5", className)}>
      <h2 className="font-display text-[15px] leading-4.5 font-semibold text-foreground">{title}</h2>
      {action ?? (meta ? <div className="text-[11px] leading-3.5 text-muted-foreground">{meta}</div> : null)}
    </div>
  );
}

/** Inline accent link used for "View all …", "Inspect …" affordances. */
export function InlineLink({
  children,
  className,
  ...props
}: React.ComponentProps<"button">) {
  return (
    <button
      type="button"
      className={cn(
        "cursor-pointer text-[11px] leading-3.5 text-accent-foreground hover:underline",
        className,
      )}
      {...props}
    >
      {children}
    </button>
  );
}
