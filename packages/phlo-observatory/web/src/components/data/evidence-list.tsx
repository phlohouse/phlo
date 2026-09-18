/**
 * Evidence list: the recurring "requirement / explanation / verdict" rows used
 * by release evidence, backup coverage and maintenance reporting.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export interface EvidenceRow {
  /** Requirement or artefact name. */
  name: React.ReactNode;
  /** Supporting explanation, shown muted. */
  detail?: React.ReactNode;
  /** Trailing verdict. */
  outcome?: React.ReactNode;
  /** Tailwind text colour for the verdict. */
  tone?: string;
}

export function EvidenceList({
  rows,
  nameWidth = "w-40",
  className,
}: {
  rows: Array<EvidenceRow>;
  /** Fixed width for the name lane so verdicts align across rows. */
  nameWidth?: string;
  className?: string;
}) {
  return (
    <div className={cn("overflow-clip rounded-[7px] border border-border", className)}>
      {rows.map((row, index) => (
        <div
          key={index}
          className={cn(
            "flex min-h-7.5 items-center px-3",
            index > 0 && "border-t border-border",
          )}
        >
          <span className={cn("shrink-0 text-xs font-medium", nameWidth)}>{row.name}</span>
          {row.detail ? (
            <span className="flex-1 truncate text-[11px] leading-3.5 text-muted-foreground">
              {row.detail}
            </span>
          ) : (
            <span className="flex-1" />
          )}
          {row.outcome ? (
            <span className={cn("shrink-0 text-right text-xs", row.tone)}>{row.outcome}</span>
          ) : null}
        </div>
      ))}
    </div>
  );
}

/** Borderless key/value list with a trailing verdict, used inside rails. */
export function EvidenceListPlain({
  rows,
  className,
}: {
  rows: Array<{ label: React.ReactNode; value: React.ReactNode; tone?: string }>;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col", className)}>
      {rows.map((row, index) => (
        <div
          key={index}
          className="flex h-6.25 items-center justify-between border-b border-border/60 text-[11px] leading-3.5 last:border-b-0"
        >
          <span>{row.label}</span>
          <span className={cn("text-muted-foreground", row.tone)}>{row.value}</span>
        </div>
      ))}
    </div>
  );
}
