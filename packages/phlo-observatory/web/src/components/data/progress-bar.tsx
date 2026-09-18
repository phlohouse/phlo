/**
 * ProgressBar: proportional result bars used by the run timeline and trace
 * waterfall, plus the consumed/remaining bars in inspectors.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

export type BarTone = "success" | "danger" | "warning" | "primary" | "muted";

const TONE: Record<BarTone, string> = {
  success: "bg-success",
  danger: "bg-destructive",
  warning: "bg-warning",
  primary: "bg-primary",
  muted: "bg-muted-foreground",
};

export function ProgressBar({
  value,
  tone = "success",
  className,
  trackClassName,
}: {
  /** Percentage 0–100. */
  value: number;
  tone?: BarTone;
  className?: string;
  trackClassName?: string;
}) {
  return (
    <span className={cn("flex h-2 w-full items-center", trackClassName)}>
      <span
        className={cn("h-2 rounded-sm opacity-70", TONE[tone], className)}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </span>
  );
}

/**
 * Offset bar for timelines: a transparent leading spacer followed by the
 * coloured result, both expressed as percentages of the total window.
 */
export function OffsetBar({
  offset,
  width,
  tone = "success",
  label,
}: {
  offset: number;
  width: number;
  tone?: BarTone;
  label?: React.ReactNode;
}) {
  if (width <= 0) {
    return <span className="text-[10px] text-muted-foreground">{label}</span>;
  }
  return (
    <span className="flex w-full items-center">
      <span style={{ width: `${offset}%` }} />
      <span
        className={cn("h-2 rounded-sm opacity-70", TONE[tone])}
        style={{ width: `${width}%` }}
      />
    </span>
  );
}

/** Thin utilisation bar with a filled proportion, used in inspector rails. */
export function MeterBar({
  value,
  tone = "primary",
  className,
}: {
  value: number;
  tone?: BarTone;
  className?: string;
}) {
  return (
    <span className={cn("block h-1.5 w-full overflow-hidden rounded-full bg-muted", className)}>
      <span
        className={cn("block h-full rounded-full", TONE[tone])}
        style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
      />
    </span>
  );
}
