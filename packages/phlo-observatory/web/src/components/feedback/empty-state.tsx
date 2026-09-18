/**
 * Empty state for tables, rails and whole pages with no data yet.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export function EmptyState({
  title,
  detail,
  action,
  className,
}: {
  title: React.ReactNode;
  detail?: React.ReactNode;
  action?: React.ReactNode;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-2 rounded-[7px] border border-dashed border-border px-6 py-10 text-center",
        className,
      )}
    >
      <span className="text-[13px] font-semibold">{title}</span>
      {detail ? (
        <span className="max-w-md text-[11px] leading-4 text-muted-foreground">{detail}</span>
      ) : null}
      {action ? <div className="pt-1">{action}</div> : null}
    </div>
  );
}
