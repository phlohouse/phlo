/**
 * Section heading and the surrounding block.
 *
 * `SectionHeader` matches Paper's repeated `flex justify-between pb-2.5`; the
 * `Section` wrapper adds the 10px rhythm between a heading and its content so
 * pages compose blocks instead of hand-rolling the same flex column.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

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
    <div className={cn("flex items-center justify-between gap-2 pb-2.5", className)}>
      <h2 className="font-display text-[15px] leading-4.5 font-semibold text-foreground">{title}</h2>
      {action ??
        (meta ? (
          <div className="text-[11px] leading-3.5 text-muted-foreground">{meta}</div>
        ) : null)}
    </div>
  );
}

/** Heading plus content, spaced on Paper's section rhythm. */
export function Section({
  title,
  meta,
  action,
  className,
  children,
}: {
  title: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("flex flex-col gap-2.5", className)}>
      <SectionHeader title={title} meta={meta} action={action} />
      {children}
    </section>
  );
}

/** Inline accent link used for "View all …", "Inspect …" affordances. */
export function InlineLink({ children, className, ...props }: React.ComponentProps<"button">) {
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
