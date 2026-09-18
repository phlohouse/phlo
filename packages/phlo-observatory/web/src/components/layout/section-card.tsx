/**
 * SectionCard: the bordered 7px block that wraps a titled group of content.
 * Provides the header band and body padding so pages do not restate them.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export function SectionCard({
  title,
  meta,
  action,
  headerClassName,
  bodyClassName,
  className,
  children,
}: {
  title?: React.ReactNode;
  meta?: React.ReactNode;
  action?: React.ReactNode;
  headerClassName?: string;
  bodyClassName?: string;
  className?: string;
  children: React.ReactNode;
}) {
  const hasHeader = Boolean(title || meta || action);
  return (
    <div className={cn("overflow-clip rounded-[7px] border border-border", className)}>
      {hasHeader ? (
        <div
          className={cn(
            "flex items-center justify-between gap-2 border-b border-border bg-accent px-3 py-2.5",
            headerClassName,
          )}
        >
          <span className="font-display text-[13px] leading-4.5 font-semibold text-accent-foreground">
            {title}
          </span>
          {action ?? (meta ? <span className="text-[11px] text-accent-foreground/80">{meta}</span> : null)}
        </div>
      ) : null}
      <div className={cn(bodyClassName)}>{children}</div>
    </div>
  );
}
