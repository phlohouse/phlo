/**
 * Page layout primitives.
 *
 * Every screen follows the same rhythm: a masthead, an optional metric band,
 * an optional tab bar, then content that may sit beside a detail rail. These
 * wrappers own that padding so routes contain composition, not spacing.
 *
 * Rules of composition:
 *   - `PageContent` supplies the page gutter (px-6) and vertical rhythm.
 *   - `SplitRow` supplies the two-column split with no padding, for use
 *     *inside* `PageContent`. Never nest `PageContent` in `PageContent`.
 */
import * as React from "react";

import { cn } from "@/lib/utils";

/** Vertical root for a page. */
export function Page({ className, children }: { className?: string; children: React.ReactNode }) {
  return <div className={cn("flex flex-col", className)}>{children}</div>;
}

/** Horizontal band that holds a metric strip, matching Paper's pb-4.5 gutter. */
export function PageBand({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn("px-6 pb-4", className)}>{children}</div>;
}

/**
 * Main column beside an optional rail. No padding — safe to nest inside
 * `PageContent`.
 */
export function SplitRow({
  rail,
  className,
  children,
}: {
  rail?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  if (!rail) {
    return <div className={cn("flex min-w-0 flex-col gap-4.5", className)}>{children}</div>;
  }
  return (
    <div className={cn("flex gap-5.5", className)}>
      <div className="flex min-w-0 flex-1 flex-col gap-4.5">{children}</div>
      {rail}
    </div>
  );
}

/** Padded content region. Use one per page; nest `SplitRow` for a rail. */
export function PageContent({
  rail,
  className,
  children,
}: {
  rail?: React.ReactNode;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div className={cn("min-w-0 px-6 pt-4.5 pb-5", className)}>
      <SplitRow rail={rail}>{children}</SplitRow>
    </div>
  );
}

/** Vertical stack of sections inside main content. */
export function PageStack({
  className,
  children,
}: {
  className?: string;
  children: React.ReactNode;
}) {
  return <div className={cn("flex min-w-0 flex-col gap-4.5", className)}>{children}</div>;
}
