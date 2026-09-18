/**
 * Detail rail: the fixed-width right column that carries context for the item
 * being viewed (run details, dataset ownership, publication plan, service
 * diagnostics). Sections stack with Paper's 18px rhythm and a hairline rule.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export function DetailRail({
  width = "w-75",
  className,
  children,
}: {
  width?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <aside
      className={cn(
        "flex shrink-0 flex-col gap-4.5 border-l border-border pl-4.5",
        width,
        className,
      )}
    >
      {children}
    </aside>
  );
}

/**
 * One block inside a rail. `title` renders as a 15px heading; `meta` and
 * `action` occupy the trailing slot; `divided` adds the top hairline used when
 * a rail holds several unrelated blocks.
 */
export function DetailSection({
  title,
  meta,
  action,
  divided = false,
  className,
  children,
}: {
  title?: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  divided?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <section className={cn("flex flex-col gap-2.5", divided && "border-t border-border pt-4", className)}>
      {title ? (
        <div className="flex items-baseline justify-between gap-2">
          <h2 className="font-display text-[15px] leading-4.5 font-semibold">{title}</h2>
          {action ?? (meta ? <span className="text-[11px] leading-3.5 text-muted-foreground">{meta}</span> : null)}
        </div>
      ) : null}
      {children}
    </section>
  );
}
